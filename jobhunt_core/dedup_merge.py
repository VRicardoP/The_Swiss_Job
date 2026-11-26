"""Fusión diaria de duplicados confirmados por regla (decisión D2, 2026-11-26).

`dedup.py` sólo DETECTA: deja pares `pending` en `dedup_candidates`. Este módulo
FUSIONA los que cumplen una regla determinista, siguiendo ADR-04:

- **Fusionar, no borrar.** El perdedor recibe `merged_into = ganador` y sale del
  corpus (todo lector filtra `merged_into IS NULL`); sus revisiones,
  encarnaciones y evaluaciones —append-only— no se tocan. El estado MUTABLE del
  usuario (feedback, descarte, guardado, notas y candidaturas) se TRASLADA al
  ganador y cada fila cambiada queda en `merge_transfers` con su imagen antes y
  después, para poder deshacerlo. Borrar perdería ese estado y el rastro.
- **Regla `merge-exact-v1`**: mismo `text_hash` (título, empresa, descripción y
  etiquetas idénticos), misma `location` literal y ningún campo de salario ni
  `remote` contradictorio. Es la regla del exacto-intra que ratificó el
  propietario (2026-08-23; holdout: H4 8/8 `duplicate`), extendida a fuentes
  distintas —el mismo texto en la misma ciudad en dos portales es la misma
  oferta con más razón que dentro de un portal— y endurecida con el veto de
  salario: el holdout del 2026-08-30 encontró dos vacantes DISTINTAS idénticas
  salvo en el salario publicado (Clera / Founding Engineer / New York). Los
  pares del ANN y del léxico que no cumplen la regla siguen `pending`: el coseno
  del texto completo mide boilerplate compartido (COTAS §2.1, coseno 1,0 entre
  un contable y un jardinero), así que su similitud no autoriza una fusión
  automática.
- **Nunca un par rechazado**: sólo se leen pares `pending`, y además se veta la
  fusión si existe un par `rejected` entre cualquier miembro de los dos grupos.
- **Ganador determinista** (`choose_winner`): con encarnación activa de una
  fuente nativa > con encarnación activa > descripción más larga > vacante más
  antigua > id menor. ADR-04 proponía «la más antigua», pero con ~42.000
  encarnaciones `legacy:*` que ya no se cosechan eso hacía ganar la copia que
  el archivado retira a los 120 días mientras la nativa sigue viva.
- **Por lotes, idempotente y reanudable**: cada lote es una transacción con
  cursor `dc.id`; lo fusionado pasa a `confirmed` con `merge_log_id`, así que
  una pasada cortada continúa sin repetir nada. Los bloqueos siguen el protocolo
  del sink (un `FOR UPDATE` ordenado por id sobre todo lo que se escribe) y un
  advisory lock serializa a los fusionadores entre sí.
- **Cadenas planas**: si un perdedor ya era ganador de otros, éstos se
  re-apuntan al nuevo ganador; `merged_into` siempre está a un salto.

Invalidación: el trigger de sentencia `trg_corpus_generation_vacancies` sube
`corpus_generation` en cada UPDATE de `merged_into`, y la versión del feed
cambia porque cambia su conjunto de vacantes. No hace falta evento de outbox.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MERGE_RULE = "rule:merge-exact-v1"
MERGE_ACTOR = "jobhunt.maintenance.dedup_merge"
# Pares PENDIENTES que examina cada lote, cumplan o no la regla. Acotar lo
# examinado —y no lo fusionado— es lo que hace lineal la pasada: con un LIMIT
# sobre lo elegible el planificador recorría TODOS los pendientes en cada lote
# (medido en local: 3,2 s por lote sobre 4.262 pendientes; producción tiene 236.000).
MERGE_BATCH_SIZE = 500
_MERGE_LOCK_KEY = "jobhunt.dedup_merge"

# Campos que, presentes en las dos ofertas con valor distinto, vetan la fusión
# aunque el texto sea idéntico: `text_hash` no los cubre (ADR-02).
CONTRADICTION_FIELDS = (
    "salary",
    "salary_min",
    "salary_max",
    "salary_min_chf",
    "salary_max_chf",
    "salary_currency",
    "remote",
)

# Estado del usuario que se traslada. `current_eval_id` NO: su FK exige una
# evaluación de la MISMA vacante, y la del ganador la pone el matching.
_PVS_USER_FIELDS = (
    "feedback",
    "feedback_recorded_at",
    "dismissed_at",
    "saved_at",
    "notes",
)

# Prefiltro en SQL (barato y con índice): pares pendientes cuyas vacantes
# EFECTIVAS (un salto de merged_into: las cadenas se mantienen planas) siguen
# vivas y comparten texto y ubicación, o ya colapsaron en la misma vacante.
# Los colegios quedan fuera: su vínculo (school_job_details) lo reescribe la
# ingesta escolar y no sigue merged_into. La regla completa la decide
# `exact_rule_holds`; esto sólo descarta lo que seguro no la cumple.
_ELIGIBLE_SQL = """
SELECT dc.id, dc.similarity, ea.id AS a, eb.id AS b
FROM dedup_candidates dc
JOIN vacancies va ON va.id = dc.vacancy_a
JOIN vacancies vb ON vb.id = dc.vacancy_b
JOIN vacancies ea ON ea.id = coalesce(va.merged_into, va.id)
JOIN vacancies eb ON eb.id = coalesce(vb.merged_into, vb.id)
JOIN offer_revisions oa ON oa.id = ea.current_offer_revision_id
JOIN offer_revisions ob ON ob.id = eb.current_offer_revision_id
WHERE dc.id = ANY(CAST(:ids AS uuid[])) AND dc.state = 'pending'
  AND ea.archived_at IS NULL AND ea.merged_into IS NULL
  AND eb.archived_at IS NULL AND eb.merged_into IS NULL
  AND (ea.id = eb.id OR (
        oa.text_hash = ob.text_hash
    AND coalesce(oa.content->>'location', '') = coalesce(ob.content->>'location', '')
    AND NOT EXISTS (SELECT 1 FROM school_job_details j
                    WHERE j.vacancy_id IN (ea.id, eb.id))))
ORDER BY dc.id
"""

# Ventana del lote: los siguientes pendientes por id (índice parcial core0061).
_WINDOW_SQL = """
SELECT id FROM dedup_candidates
WHERE state = 'pending' {after}
ORDER BY id LIMIT :n
"""

_PROFILE_SQL = """
SELECT v.id, v.created_at, o.text_hash, o.content,
       EXISTS (SELECT 1 FROM source_listing_incarnations i
               WHERE i.vacancy_id = v.id AND i.ended_at IS NULL) AS live,
       EXISTS (SELECT 1 FROM source_listing_incarnations i
               JOIN source_listings sl ON sl.id = i.source_listing_id
               JOIN sources s ON s.id = sl.source_id
               WHERE i.vacancy_id = v.id AND i.ended_at IS NULL
                 AND s.name NOT LIKE 'legacy:%') AS native_live
FROM vacancies v
LEFT JOIN offer_revisions o ON o.id = v.current_offer_revision_id
WHERE v.id = ANY(:ids)
"""


@dataclass(frozen=True)
class VacancyProfile:
    """Lo que la regla y el criterio de ganador necesitan de una vacante."""

    id: uuid.UUID
    created_at: datetime
    text_hash: str | None
    content: dict
    live: bool
    native_live: bool


def exact_rule_holds(a: VacancyProfile, b: VacancyProfile) -> bool:
    """`merge-exact-v1`: mismo texto, misma ubicación literal, nada contradictorio."""
    if a.text_hash is None or a.text_hash != b.text_hash:
        return False
    if (a.content.get("location") or "") != (b.content.get("location") or ""):
        return False
    for field in CONTRADICTION_FIELDS:
        va, vb = a.content.get(field), b.content.get(field)
        if va is not None and vb is not None and va != vb:
            return False
    return True


def choose_winner(
    a: VacancyProfile, b: VacancyProfile
) -> tuple[VacancyProfile, VacancyProfile]:
    """(ganador, perdedor). Determinista y simétrico: no depende del orden."""

    def key(v: VacancyProfile):
        return (
            not v.native_live,
            not v.live,
            -len(v.content.get("description") or ""),
            v.created_at,
            str(v.id),
        )

    winner, loser = sorted((a, b), key=key)
    return winner, loser


def _jsonable(row: dict) -> dict:
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()
    }


def _merged_pvs(winner_row: dict | None, loser_row: dict) -> dict:
    """Estado de usuario resultante en el ganador (ADR-04: gana el más fuerte).

    Feedback: el más reciente de los dos. Descarte, guardado y notas: el del
    ganador si lo tiene, si no el del perdedor.
    """
    w = dict.fromkeys(_PVS_USER_FIELDS) if winner_row is None else dict(winner_row)
    out = {k: w[k] for k in _PVS_USER_FIELDS}
    if loser_row["feedback"] is not None:
        lt, wt = loser_row["feedback_recorded_at"], w["feedback_recorded_at"]
        if w["feedback"] is None or (lt is not None and (wt is None or lt > wt)):
            out["feedback"] = loser_row["feedback"]
            out["feedback_recorded_at"] = lt
    for field in ("dismissed_at", "saved_at", "notes"):
        if out[field] is None:
            out[field] = loser_row[field]
    return out


async def _transfer_pvs(session, winner, loser) -> list[dict]:
    """Traslada el estado de usuario de `profile_vacancy_state` al ganador."""
    cols = ", ".join(_PVS_USER_FIELDS)
    loser_rows = (
        (
            await session.execute(
                sa.text(
                    f"SELECT profile_id, {cols} FROM profile_vacancy_state "
                    "WHERE vacancy_id = :v AND (feedback IS NOT NULL "
                    "  OR dismissed_at IS NOT NULL OR saved_at IS NOT NULL "
                    "  OR notes IS NOT NULL) "
                    "ORDER BY profile_id FOR UPDATE"
                ),
                {"v": loser},
            )
        )
        .mappings()
        .all()
    )
    if not loser_rows:
        return []
    winner_rows = {
        r["profile_id"]: dict(r)
        for r in (
            await session.execute(
                sa.text(
                    f"SELECT profile_id, {cols} FROM profile_vacancy_state "
                    "WHERE vacancy_id = :v AND profile_id = ANY(:p) "
                    "ORDER BY profile_id FOR UPDATE"
                ),
                {"v": winner, "p": [r["profile_id"] for r in loser_rows]},
            )
        )
        .mappings()
        .all()
    }
    transfers = []
    for lr in loser_rows:
        lr = dict(lr)
        pid = lr["profile_id"]
        before_w = winner_rows.get(pid)
        after_w = _merged_pvs(before_w, lr)
        await session.execute(
            sa.text(
                "INSERT INTO profile_vacancy_state "
                f"(profile_id, vacancy_id, {cols}, updated_at) "
                "VALUES (:p, :v, :feedback, :feedback_recorded_at, :dismissed_at, "
                "        :saved_at, :notes, clock_timestamp()) "
                "ON CONFLICT (profile_id, vacancy_id) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in _PVS_USER_FIELDS)
                + ", updated_at = GREATEST(profile_vacancy_state.updated_at, "
                "  clock_timestamp())"
            ),
            {"p": pid, "v": winner, **after_w},
        )
        # Se MUEVE, no se copia: el bookmark puro no filtra fusionadas y el
        # usuario vería la misma oferta guardada dos veces.
        await session.execute(
            sa.text(
                "UPDATE profile_vacancy_state SET "
                + ", ".join(f"{c} = NULL" for c in _PVS_USER_FIELDS)
                + ", updated_at = GREATEST(updated_at, clock_timestamp()) "
                "WHERE profile_id = :p AND vacancy_id = :v"
            ),
            {"p": pid, "v": loser},
        )
        before_l = {k: lr[k] for k in _PVS_USER_FIELDS}
        transfers.append(
            {
                "entity": "profile_vacancy_state",
                "row_key": f"{pid}:{winner}",
                "profile_id": pid,
                "before": None
                if before_w is None
                else {k: before_w[k] for k in _PVS_USER_FIELDS},
                "after": after_w,
            }
        )
        transfers.append(
            {
                "entity": "profile_vacancy_state",
                "row_key": f"{pid}:{loser}",
                "profile_id": pid,
                "before": before_l,
                "after": dict.fromkeys(_PVS_USER_FIELDS),
            }
        )
    return transfers


async def _transfer_applications(session, winner, loser) -> list[dict]:
    """Re-apunta las candidaturas del perdedor al ganador si el perfil no tiene
    ya una allí (UNIQUE perfil+vacante). Con conflicto se conservan las dos: son
    dos candidaturas reales del usuario y ninguna se descarta. La encarnación se
    suelta (su FK exige la misma vacante); el `snapshot` conserva lo que se vio.
    """
    rows = (
        (
            await session.execute(
                sa.text(
                    "UPDATE applications a SET vacancy_id = :w, "
                    "  source_listing_incarnation_id = NULL, "
                    "  updated_at = GREATEST(a.updated_at, clock_timestamp()) "
                    "FROM applications old "
                    "WHERE a.id = old.id AND a.vacancy_id = :l "
                    "  AND NOT EXISTS (SELECT 1 FROM applications w "
                    "                  WHERE w.profile_id = a.profile_id "
                    "                    AND w.vacancy_id = :w) "
                    "RETURNING a.id, a.profile_id, "
                    "  old.source_listing_incarnation_id AS old_inc"
                ),
                {"w": winner, "l": loser},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "entity": "applications",
            "row_key": str(r["id"]),
            "profile_id": r["profile_id"],
            "before": {
                "vacancy_id": str(loser),
                "source_listing_incarnation_id": None
                if r["old_inc"] is None
                else str(r["old_inc"]),
            },
            "after": {"vacancy_id": str(winner), "source_listing_incarnation_id": None},
        }
        for r in rows
    ]


async def load_rejected(session) -> set[frozenset]:
    """Pares rechazados (~1.500): se cargan una vez por fragmento."""
    return {
        frozenset((r.vacancy_a, r.vacancy_b))
        for r in (
            await session.execute(
                sa.text(
                    "SELECT vacancy_a, vacancy_b FROM dedup_candidates "
                    "WHERE state = 'rejected'"
                )
            )
        ).all()
    }


async def merge_batch(
    session: AsyncSession,
    *,
    after_id: uuid.UUID | None = None,
    batch_size: int = MERGE_BATCH_SIZE,
    rejected: set[frozenset] | None = None,
) -> dict:
    """Fusiona UN lote de pares elegibles en la transacción de `session`.

    El llamante hace commit. Devuelve el cursor para el lote siguiente
    (`next_after`) y `complete=True` cuando no quedan pares tras el cursor.
    """
    await session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": _MERGE_LOCK_KEY},
    )
    if rejected is None:
        rejected = await load_rejected(session)
    after = "AND id > CAST(:after AS uuid)" if after_id is not None else ""
    window = list(
        (
            await session.execute(
                sa.text(_WINDOW_SQL.format(after=after)),
                {"after": str(after_id) if after_id else None, "n": batch_size},
            )
        ).scalars()
    )
    stats = {"merged": 0, "collapsed": 0, "vetoed": 0, "transfers": 0}
    if not window:
        return {**stats, "complete": True, "next_after": None}
    done = {"complete": len(window) < batch_size, "next_after": window[-1]}
    ids = [str(i) for i in window]
    pre = (await session.execute(sa.text(_ELIGIBLE_SQL), {"ids": ids})).all()
    if not pre:
        return {**stats, **done}

    roots = {r.a for r in pre} | {r.b for r in pre}
    members: dict[uuid.UUID, set] = {v: {v} for v in roots}
    for m in (
        await session.execute(
            sa.text(
                "SELECT id, merged_into FROM vacancies WHERE merged_into = ANY(:r)"
            ),
            {"r": list(roots)},
        )
    ).all():
        members[m.merged_into].add(m.id)
    # Protocolo de locks del sink: UN FOR UPDATE ordenado por id sobre todo lo
    # que este lote puede escribir (raíces y sus perdedores, que se re-apuntan).
    await session.execute(
        sa.text("SELECT id FROM vacancies WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"),
        {"ids": sorted(set().union(*members.values()), key=str)},
    )
    # Bajo el lock se relee: entre el prefiltro y el lock la cosecha pudo
    # cambiar la revisión vigente de alguna.
    rows = (
        await session.execute(
            sa.text(_ELIGIBLE_SQL),
            {"ids": [str(r.id) for r in pre]},
        )
    ).all()
    profiles = {
        r.id: VacancyProfile(
            id=r.id,
            created_at=r.created_at,
            text_hash=r.text_hash,
            content=r.content or {},
            live=r.live,
            native_live=r.native_live,
        )
        for r in (
            await session.execute(sa.text(_PROFILE_SQL), {"ids": list(roots)})
        ).all()
    }

    redirect: dict[uuid.UUID, uuid.UUID] = {}  # perdedor de ESTE lote → ganador

    def root(v):
        while v in redirect:
            v = redirect[v]
        return v

    confirmed = []
    merge_rows, transfer_rows, pointer = [], [], {}
    for r in rows:
        a, b = root(r.a), root(r.b)
        if a not in profiles or b not in profiles:
            continue  # imposible con el advisory lock; sin perfil no se decide
        if a == b:
            confirmed.append({"id": r.id, "log": None})
            stats["collapsed"] += 1
            continue
        pa, pb = profiles[a], profiles[b]
        group_a, group_b = members[a], members[b]
        if not exact_rule_holds(pa, pb) or any(
            frozenset((x, y)) in rejected for x in group_a for y in group_b
        ):
            stats["vetoed"] += 1
            continue
        winner, loser = choose_winner(pa, pb)
        log_id = uuid.uuid4()
        transfers = []
        for v in sorted(members[loser.id], key=str):
            before = pointer.get(v, loser.id if v != loser.id else None)
            pointer[v] = winner.id
            transfers.append(
                {
                    "entity": "vacancies.merged_into",
                    "row_key": str(v),
                    "profile_id": None,
                    "before": {"merged_into": None if before is None else str(before)},
                    "after": {"merged_into": str(winner.id)},
                }
            )
        transfers += await _transfer_pvs(session, winner.id, loser.id)
        transfers += await _transfer_applications(session, winner.id, loser.id)
        members[winner.id] |= members.pop(loser.id)
        redirect[loser.id] = winner.id
        merge_rows.append(
            {
                "id": log_id,
                "w": winner.id,
                "l": loser.id,
                "ev": json.dumps(
                    {
                        "rule": MERGE_RULE,
                        "candidate_id": str(r.id),
                        "text_hash": winner.text_hash,
                        "similarity": None
                        if r.similarity is None
                        else float(r.similarity),
                    }
                ),
                "conf": r.similarity,
                "actor": MERGE_ACTOR,
            }
        )
        transfer_rows += [
            {
                "mid": log_id,
                "entity": t["entity"],
                "key": t["row_key"],
                "pid": t["profile_id"],
                "before": json.dumps(
                    None if t["before"] is None else _jsonable(t["before"])
                ),
                "after": json.dumps(_jsonable(t["after"])),
            }
            for t in transfers
        ]
        confirmed.append({"id": r.id, "log": log_id})
        stats["merged"] += 1

    if pointer:
        # UNA sentencia: un solo bump de corpus_generation por lote.
        await session.execute(
            sa.text(
                "UPDATE vacancies v SET merged_into = p.w FROM "
                "unnest(CAST(:ids AS uuid[]), CAST(:ws AS uuid[])) AS p(id, w) "
                "WHERE v.id = p.id"
            ),
            {
                "ids": [str(k) for k in pointer],
                "ws": [str(w) for w in pointer.values()],
            },
        )
    if merge_rows:
        await session.execute(
            sa.text(
                "INSERT INTO merge_log (id, winner_id, loser_id, evidence, confidence, actor) "
                "VALUES (:id, :w, :l, CAST(:ev AS jsonb), :conf, :actor)"
            ),
            merge_rows,
        )
    if transfer_rows:
        await session.execute(
            sa.text(
                "INSERT INTO merge_transfers "
                "(merge_log_id, entity, row_key, profile_id, before, after) "
                "VALUES (:mid, :entity, :key, :pid, CAST(:before AS jsonb), "
                "        CAST(:after AS jsonb))"
            ),
            transfer_rows,
        )
    if confirmed:
        await session.execute(
            sa.text(
                "UPDATE dedup_candidates SET state = 'confirmed', "
                "  resolved_by = :rule, resolved_at = statement_timestamp(), "
                "  merge_log_id = :log WHERE id = :id AND state = 'pending'"
            ),
            [{**c, "rule": MERGE_RULE} for c in confirmed],
        )
    stats["transfers"] = len(transfer_rows)
    return {**stats, **done}
