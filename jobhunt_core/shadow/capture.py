"""Consumidor CDC legacy→core de la sombra (B-01, CONTRATOS_FASE_B.md §2).

Proceso DEDICADO (servicio `core-capture`, no worker Celery) sobre psycopg2:

- **Bootstrap primera vez**: readiness del esquema legacy ANTES de crear el
  slot (las migraciones legacy son MANUALES: si las tablas capturadas no
  existen aún con su PK contractual se espera con backoff SIN crear nada —
  un slot creado en frío retendría WAL en cada vuelta del crash-loop, §8) →
  `CREATE_REPLICATION_SLOT ... LOGICAL wal2json
  EXPORT_SNAPSHOT` → BACKFILL de las tablas capturadas leyendo CON ese
  snapshot (`SET TRANSACTION SNAPSHOT` en la conexión normal) hacia
  `shadow_change_log` (op='I', lsn=snapshot_lsn, seq incremental) → frontera
  snapshot↔LSN registrada en `shadow_capture_state` EN LA MISMA transacción
  (atómico: un bootstrap interrumpido no deja estado a medias) →
  START_REPLICATION. Si el slot ya existe con estado: reanuda desde
  `last_applied_lsn`.
- **Streaming wal2json v2**: por transacción (B..C) aplica la WHITELIST de
  columnas POR TABLA (§2), INSERT idempotente ON CONFLICT (lsn, seq_in_tx)
  DO NOTHING, COMMIT, y SOLO DESPUÉS `send_feedback(flush_lsn)` — ack tras
  commit: pérdida imposible por diseño; la re-entrega (posible por diseño,
  p.ej. al reanudar en el LSN del último commit aplicado) la absorbe la PK.
- **SIN filtro de no-cambios** (§2): REPLICA IDENTITY default no trae valores
  viejos y un filtro por contenido perdería `is_active`/`duplicate_of` — se
  stagea TODO; los no-cambios los absorbe el pre-filtro del sink (B-02).
- **TOAST (§2/§8)**: con REPLICA IDENTITY default wal2json OMITE del mensaje
  U las columnas TOASTeadas que el UPDATE no tocó (AUSENTES ≠ NULL). El
  payload stageado registra en la clave meta `_omitted` las columnas
  whitelisted ausentes del mensaje (I/U; sin la clave = mensaje completo) —
  B-02 sabe qué preservar. `user_profiles` se COMPLETA en staging re-leyendo
  las ausentes por PK con la conexión normal (SELECT RO de §1), marcándolas
  además en `_backfilled`; el valor re-leído puede ser MÁS NUEVO que el del
  mensaje (convergente: cualquier cambio posterior también llega por WAL).
  `jobs` NO se re-lee: el pre-filtro por content_hash del sink absorbe los
  updates sin cambio de contenido y un cambio real de contenido llega con la
  description completa en el upsert de cosecha.

Dos conexiones y dos roles: la de REPLICACIÓN (`jobhunt_capture`, CAPTURE_DSN
— la decodificación lógica no pasa por ACL de tabla) y la NORMAL
(`jobhunt_core`, CORE_DATABASE_URL — SELECT whitelisted sobre el legacy para
el backfill + escritura del staging propio). El snapshot exportado por una se
importa desde la otra (válido mientras la conexión de replicación no ejecute
otro comando — por eso el backfill ocurre ANTES de START_REPLICATION).

- **Heartbeat (P2-7, core0009)**: `shadow_capture_state.heartbeat_at` es la
  señal de LIVENESS del consumidor — se actualiza en cada keepalive del
  stream y en cada tx aplicada (también en las vacías). `updated_at`/
  `last_applied_lsn` son progreso de DATOS: el healthcheck ya no da falsos
  unhealthy con slot activo y días sin tráfico legacy.

Slot y tabla-lista PARAMETRIZABLES (tests: esquema fixture en BD desechable;
producción: defaults del contrato). LSN como BIGINT: pg_lsn 'X/Y' =
(X<<32)|Y — el mismo entero que expone psycopg2 (msg.data_start).
"""

import json
import logging
import os
import re
import select as select_mod
import signal
import sys
import time

import psycopg2
import psycopg2.extras

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_SLOT = "jobhunt_shadow"
DEFAULT_TABLES = "public.jobs,public.user_profiles,public.users"

# Whitelist de columnas POR TABLA (§2) — whitelist, no blacklist: una columna
# nueva del legacy NO entra sola al staging. `salary_*` expandido a las 5
# columnas reales del modelo legacy. users: SOLO id/is_active —
# hashed_password/email/gdpr_* JAMÁS llegan al staging (riesgo GDPR, §8).
# Las columnas pesadas no contractuales (embedding, search_vector,
# cv_embedding) quedan fuera: el core computa los SUYOS.
TABLE_WHITELIST: dict[str, dict] = {
    "jobs": {
        "pk": "hash",
        "columns": frozenset({
            "title", "company", "description", "tags", "location", "canton",
            "language", "seniority", "contract_type", "remote",
            "salary_min_chf", "salary_max_chf", "salary_original",
            "salary_currency", "salary_period", "url", "source",
            "is_active", "duplicate_of", "content_hash",
        }),
    },
    "user_profiles": {
        "pk": "id",
        "columns": frozenset({"user_id", "title", "cv_text", "skills", "updated_at"}),
        # TOAST (§2/§8): cv_text es contractual para canónica/embeddings — si
        # wal2json lo omite (UPDATE que no lo toca), se COMPLETA re-leyendo
        # por PK con la conexión normal (SELECT RO de §1). jobs NO lleva esta
        # marca: sus omisiones las absorbe el pre-filtro del sink (§2).
        "reread_omitted": True,
    },
    "users": {
        "pk": "id",
        "columns": frozenset({"id", "is_active"}),
    },
}

# Los comandos de replicación no admiten parámetros bind: la validación de
# identificadores es la barrera (misma disciplina que migrate.py).
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def lsn_to_int(lsn: str) -> int:
    """pg_lsn 'X/Y' → BIGINT (X<<32 | Y): el entero del protocolo."""
    hi, lo = lsn.split("/")
    return (int(hi, 16) << 32) | int(lo, 16)


def int_to_lsn(value: int) -> str:
    """BIGINT → 'X/Y' para logs legibles."""
    return f"{value >> 32:X}/{value & 0xFFFFFFFF:X}"


def _json_dumps(obj) -> str:
    # datetime/Decimal/UUID del backfill → representación textual estable.
    return json.dumps(obj, default=str)


class ShadowCapture:
    """Ciclo de vida: start() (bootstrap o reanudación) → stream() → close().

    `run()` es el bucle de producción: reconexión con backoff exponencial y
    parada limpia por SIGTERM (stop()).
    """

    def __init__(
        self,
        capture_dsn: str,
        core_dsn: str,
        slot: str = DEFAULT_SLOT,
        tables: str = DEFAULT_TABLES,
        schema: str | None = None,
        status_interval: float = 10.0,
        ready_max_retries: int | None = None,
    ) -> None:
        if not _IDENT_RE.match(slot):
            raise ValueError(f"Nombre de slot inválido: {slot!r}")
        self.tables = [t.strip() for t in tables.split(",") if t.strip()]
        for qualified in self.tables:
            if not _TABLE_RE.match(qualified):
                raise ValueError(f"Tabla capturada inválida: {qualified!r}")
        self.schema = schema or settings.CORE_DB_SCHEMA
        if not _IDENT_RE.match(self.schema):
            raise ValueError(f"Esquema inválido: {self.schema!r}")
        self.capture_dsn = capture_dsn
        self.core_dsn = core_dsn
        self.slot = slot
        self.status_interval = status_interval
        # None = espera INDEFINIDA a que el esquema legacy migre (producción:
        # las migraciones legacy son manuales); los tests acotan con un valor
        # pequeño para probar el reintento sin colgar la suite.
        self.ready_max_retries = ready_max_retries
        self.rows_applied = 0  # total aplicado en esta instancia (observabilidad/tests)
        self._stop = False
        self._ack = True
        self._core = None  # conexión normal (staging + backfill)
        self._repl = None  # conexión de replicación
        self._cur = None  # cursor de replicación
        self._tx: list[tuple] = []  # cambios de la tx wal2json en curso
        self._seq = 0  # seq_in_tx dentro de la tx en curso

    # ------------------------------------------------------------- conexión

    def start(self, from_lsn: int | None = None) -> None:
        """Conecta y deja el stream ARRANCADO (bootstrap o reanudación).

        `from_lsn` (tests y ensayo de replay de §6) fuerza el punto de
        arranque; en producción se reanuda desde `last_applied_lsn` — el
        servidor nunca retrocede de su confirmed_flush, y la tx frontera
        puede re-entregarse: la absorbe el ON CONFLICT.
        """
        self._core = psycopg2.connect(
            self.core_dsn, options=f"-c search_path={self.schema},public"
        )
        self._core.autocommit = True  # transacciones explícitas (BEGIN/COMMIT)
        self._repl = psycopg2.connect(
            self.capture_dsn,
            connection_factory=psycopg2.extras.LogicalReplicationConnection,
        )
        self._cur = self._repl.cursor()

        state = self._read_state()
        slot_exists = self._slot_exists()
        if state is not None and state["slot_name"] != self.slot:
            raise RuntimeError(
                f"shadow_capture_state pertenece al slot {state['slot_name']!r}, "
                f"no a {self.slot!r}: una sola captura por esquema (id=1)"
            )
        if state is not None and not slot_exists:
            raise RuntimeError(
                f"Estado registrado pero slot {self.slot!r} AUSENTE: continuidad "
                "WAL perdida — ejecutar el runbook de rollback/replay de §6 "
                "(truncar staging + re-bootstrap)"
            )
        if state is not None:
            start_lsn = from_lsn if from_lsn is not None else state["last_applied_lsn"]
            logger.info(
                "Reanudando slot %s desde %s (last_applied=%s)",
                self.slot,
                int_to_lsn(start_lsn),
                int_to_lsn(state["last_applied_lsn"]),
            )
        else:
            if slot_exists:
                # Bootstrap interrumpido: el backfill+estado es atómico, así
                # que un slot sin estado es huérfano — se recrea desde cero.
                logger.warning(
                    "Slot %s sin estado registrado (bootstrap interrumpido): "
                    "DROP y re-bootstrap",
                    self.slot,
                )
                self._cur.drop_replication_slot(self.slot)
            start_lsn = self._bootstrap()

        self._tx, self._seq = [], 0
        self._cur.start_replication(
            slot_name=self.slot,
            decode=True,
            start_lsn=start_lsn,
            options={
                "format-version": "2",
                "add-tables": ",".join(self.tables),
            },
        )
        logger.info(
            "Streaming wal2json v2 arrancado (slot=%s, tablas=%s)",
            self.slot,
            ",".join(self.tables),
        )

    def stop(self) -> None:
        """Parada limpia (SIGTERM): el bucle de stream sale en ≤1 s."""
        self._stop = True

    def close(self) -> None:
        for conn in (self._cur, self._repl, self._core):
            try:
                if conn is not None:
                    conn.close()
            except Exception:  # cerrar jamás enmascara el error original
                pass
        self._cur = self._repl = self._core = None

    # ------------------------------------------------------------ bootstrap

    def _bootstrap(self) -> int:
        """Slot + snapshot exportado + backfill consistente (§2). Devuelve el
        LSN de arranque del streaming (= consistent_point del slot)."""
        self._wait_legacy_ready()
        self._cur.execute(
            f'CREATE_REPLICATION_SLOT "{self.slot}" LOGICAL wal2json EXPORT_SNAPSHOT'
        )
        slot_name, consistent_point, snapshot_name, _plugin = self._cur.fetchone()
        snapshot_lsn = lsn_to_int(consistent_point)
        logger.info(
            "Slot %s creado en %s; backfill con snapshot exportado %s",
            slot_name,
            consistent_point,
            snapshot_name,
        )
        # El snapshot exportado vive mientras la conexión de replicación no
        # ejecute OTRO comando: el backfill completo va antes del START.
        self._backfill(snapshot_name, snapshot_lsn)
        return snapshot_lsn

    def _wait_legacy_ready(self) -> None:
        """Readiness del esquema legacy ANTES de CREATE_REPLICATION_SLOT.

        Las migraciones legacy son MANUALES: en un arranque en frío las
        tablas capturadas pueden no existir todavía. Crear el slot y reventar
        después en el backfill dejaría en cada vuelta del crash-loop un slot
        huérfano reteniendo WAL de la BD compartida (§8) — aquí se espera con
        backoff SIN crear nada. `ready_max_retries=None` (producción) espera
        indefinidamente; los tests lo acotan y reciben RuntimeError."""
        backoff = 1.0
        attempts = 0
        while not self._stop:
            missing = self._legacy_missing()
            if not missing:
                return
            attempts += 1
            if (
                self.ready_max_retries is not None
                and attempts >= self.ready_max_retries
            ):
                raise RuntimeError(
                    f"esquema legacy aún sin migrar tras {attempts} "
                    f"comprobaciones (faltan: {', '.join(missing)})"
                )
            logger.warning(
                "esquema legacy aún sin migrar (faltan: %s) — "
                "reintentando en %.0fs",
                ", ".join(missing),
                backoff,
            )
            self._sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        raise RuntimeError(
            "parada solicitada durante la espera del esquema legacy "
            "(bootstrap abortado sin crear el slot)"
        )

    def _legacy_missing(self) -> list[str]:
        """Tablas capturadas AÚN no listas: ausentes o sin su PK contractual
        (misma consulta de information_schema que usa el backfill)."""
        missing = []
        with self._core.cursor() as cur:
            for qualified in self.tables:
                src_schema, table = qualified.split(".")
                spec = TABLE_WHITELIST.get(table)
                if spec is None:
                    continue  # sin whitelist contractual: el backfill la ignora
                if spec["pk"] not in self._table_columns(cur, src_schema, table):
                    missing.append(qualified)
        return missing

    def _backfill(self, snapshot_name: str, snapshot_lsn: int) -> None:
        """Lee las tablas capturadas CON el snapshot del slot y las vuelca a
        staging (op='I', lsn=snapshot_lsn, seq incremental) + frontera en
        shadow_capture_state — TODO en una única transacción."""
        seq = 0
        with self._core.cursor() as cur:
            cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            try:
                cur.execute("SET TRANSACTION SNAPSHOT %s", (snapshot_name,))
                for qualified in self.tables:
                    src_schema, table = qualified.split(".")
                    spec = TABLE_WHITELIST.get(table)
                    if spec is None:
                        logger.warning(
                            "Tabla %s sin whitelist contractual: fuera del backfill",
                            qualified,
                        )
                        continue
                    seq = self._backfill_table(
                        cur, src_schema, table, spec, snapshot_lsn, seq
                    )
                cur.execute(
                    "INSERT INTO shadow_capture_state "
                    "(id, slot_name, snapshot_lsn, snapshot_exported_at, "
                    "last_applied_lsn, heartbeat_at) "
                    "VALUES (1, %s, %s, now(), %s, now())",
                    (self.slot, snapshot_lsn, snapshot_lsn),
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        logger.info(
            "Backfill completo: %d filas op='I' en lsn=%s; frontera registrada",
            seq,
            int_to_lsn(snapshot_lsn),
        )

    def _backfill_table(self, cur, src_schema, table, spec, snapshot_lsn, seq) -> int:
        """Backfill de UNA tabla; devuelve el seq siguiente. La whitelist se
        intersecta con las columnas reales (los fixtures de test son "mini"
        tablas; en producción están todas)."""
        existing = self._table_columns(cur, src_schema, table)
        pk_col = spec["pk"]
        if pk_col not in existing:
            raise RuntimeError(f"{src_schema}.{table} sin su PK contractual {pk_col!r}")
        selected = sorted(spec["columns"] & existing)
        col_list = ", ".join([f'"{pk_col}"'] + [f'"{c}"' for c in selected])
        # Cursor DECLARE del lado servidor (dentro de la tx del snapshot):
        # jobs puede crecer sin cargar la tabla entera en memoria del
        # consumidor. DECLARE explícito y no cursor con nombre de psycopg2:
        # este gestiona la transacción a mano (autocommit + BEGIN).
        cur.execute(
            f'DECLARE backfill_read CURSOR FOR '
            f'SELECT {col_list} FROM "{src_schema}"."{table}"'
        )
        while True:
            cur.execute("FETCH FORWARD 500 FROM backfill_read")
            rows = cur.fetchall()
            if not rows:
                break
            batch = []
            for row in rows:
                payload = dict(zip(selected, row[1:]))
                batch.append(
                    (
                        snapshot_lsn,
                        seq,
                        table,
                        "I",
                        str(row[0]),
                        psycopg2.extras.Json(payload, dumps=_json_dumps),
                    )
                )
                seq += 1
            self._insert_changes(cur, batch)
        cur.execute("CLOSE backfill_read")
        return seq

    @staticmethod
    def _table_columns(cur, src_schema: str, table: str) -> set[str]:
        """Columnas reales de la tabla vía information_schema (conjunto vacío
        = tabla ausente) — compartida por backfill y check de readiness."""
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (src_schema, table),
        )
        return {row[0] for row in cur.fetchall()}

    @staticmethod
    def _insert_changes(cur, rows: list[tuple]) -> None:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO shadow_change_log "
            "(lsn, seq_in_tx, src_table, op, pk, payload) VALUES %s "
            "ON CONFLICT (lsn, seq_in_tx) DO NOTHING",
            rows,
        )

    # ------------------------------------------------------------ streaming

    def stream(self, max_seconds: float | None = None, ack: bool = True) -> None:
        """Procesa mensajes hasta stop() o `max_seconds` (tests).

        `ack=False` SOLO para tests: aplica sin send_feedback — demuestra la
        re-entrega del slot tras un kill −9 (DoD B-01).
        """
        self._ack = ack
        deadline = time.monotonic() + max_seconds if max_seconds else None
        last_status = time.monotonic()
        while not self._stop:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return
            msg = self._cur.read_message()
            if msg is not None:
                self._handle_message(msg)
                continue
            if ack and now - last_status >= self.status_interval:
                self._cur.send_feedback()  # keepalive de estado (sin avanzar flush)
                # P2-7: LATIDO en cada keepalive — liveness aunque el legacy
                # lleve días sin tráfico (updated_at solo avanza con txs).
                self._touch_heartbeat()
                last_status = now
            timeout = 1.0
            if deadline is not None:
                timeout = min(timeout, max(0.0, deadline - now))
            select_mod.select([self._cur], [], [], timeout)

    def _handle_message(self, msg) -> None:
        data = json.loads(msg.payload)
        action = data.get("action")
        if action == "B":
            self._tx, self._seq = [], 0
        elif action in ("I", "U", "D"):
            row = self._change_row(msg.data_start, data)
            if row is not None:
                self._tx.append(row)
        elif action == "C":
            self._flush(msg)
        elif action == "T":
            logger.warning("TRUNCATE en el stream (no contractual): ignorado")
        # 'M' (mensajes lógicos) y desconocidos: sin efecto en staging.

    def _change_row(self, lsn: int, data: dict) -> tuple | None:
        table = data["table"]
        spec = TABLE_WHITELIST.get(table)
        if spec is None:
            # add-tables ya filtra en el servidor; esto es defensa en
            # profundidad si la parametrización difiere del contrato.
            logger.warning("Cambio en tabla no contractual %s: descartado", table)
            return None
        op = data["action"]
        columns = self._column_map(data.get("columns"))
        identity = self._column_map(data.get("identity"))
        pk_value = columns.get(spec["pk"], identity.get(spec["pk"]))
        if pk_value is None:
            logger.error("Cambio %s en %s sin PK %s: descartado", op, table, spec["pk"])
            return None
        payload = {k: v for k, v in columns.items() if k in spec["columns"]}
        if op in ("I", "U"):
            # TOAST (§2/§8): wal2json OMITE del mensaje las columnas
            # TOASTeadas que el UPDATE no tocó — AUSENTE ≠ NULL (un NULL real
            # SÍ viaja en `columns`). Se deja SIEMPRE constancia de qué
            # columnas whitelisted faltan (B-02 sabrá qué preservar); la
            # clave meta solo aparece cuando hay ausencias: sin `_omitted`
            # el mensaje fue completo. En op=D no hay imagen nueva por
            # definición (payload {} contractual, la PK va en su columna).
            omitted = sorted(spec["columns"] - columns.keys())
            if omitted:
                payload["_omitted"] = omitted
                if spec.get("reread_omitted"):
                    backfilled = self._reread_omitted(
                        data.get("schema", "public"), table, spec, pk_value,
                        omitted, payload,
                    )
                    if backfilled:
                        payload["_backfilled"] = backfilled
        row = (
            lsn,
            self._seq,
            table,
            op,
            str(pk_value),
            psycopg2.extras.Json(payload, dumps=_json_dumps),
        )
        self._seq += 1
        return row

    def _reread_omitted(
        self,
        src_schema: str,
        table: str,
        spec: dict,
        pk_value,
        omitted: list[str],
        payload: dict,
    ) -> list[str]:
        """Completa por re-lectura RO las columnas TOAST omitidas (§2/§8).

        Solo tablas con `reread_omitted` (user_profiles): jobhunt_core tiene
        SELECT RO sobre public.user_profiles (§1) y cv_text es contractual —
        sin él B-02 confundiría la ausencia con NULL (el caso real: el PUT
        parcial de /profile o analyze_cv_and_autofill del legacy no tocan
        cv_text y wal2json lo omite del mensaje U).

        El valor re-leído puede ser MÁS NUEVO que el del mensaje (un UPDATE
        posterior ya commiteado en el legacy): semántica ACEPTADA y
        convergente — ese cambio posterior también llega por WAL y volverá a
        stagearse. Si la fila ya no existe (DELETE posterior) o la re-lectura
        falla, no se completa nada: las columnas quedan solo en `_omitted`
        (sin `_backfilled`) y B-02 preserva el valor previo.
        """
        if not _IDENT_RE.match(src_schema):
            logger.error("Esquema origen inválido %r: sin re-lectura", src_schema)
            return []
        # omitted ⊆ whitelist contractual y table/pk vienen de TABLE_WHITELIST:
        # identificadores fijos del contrato, interpolación segura.
        col_list = ", ".join(f'"{c}"' for c in omitted)
        try:
            with self._core.cursor() as cur:
                cur.execute(
                    f'SELECT {col_list} FROM "{src_schema}"."{table}" '
                    f'WHERE "{spec["pk"]}" = %s',
                    (str(pk_value),),
                )
                db_row = cur.fetchone()
        except psycopg2.Error as exc:
            logger.error(
                "Re-lectura RO de %s.%s pk=%s fallida (%s): %s solo en _omitted",
                src_schema, table, pk_value, exc, omitted,
            )
            return []
        if db_row is None:
            return []
        for name, value in zip(omitted, db_row):
            payload[name] = value
        return list(omitted)

    @staticmethod
    def _column_map(cols: list | None) -> dict:
        """[{name,type,value}] → {name: value}; json/jsonb llegan como TEXTO
        en wal2json y se re-parsean para que el staging guarde la MISMA forma
        que el backfill (listas/objetos reales, no strings anidados)."""
        out = {}
        for col in cols or []:
            value = col.get("value")
            if value is not None and isinstance(value, str) and (
                col.get("type", "").startswith("json")
            ):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass  # se conserva el texto crudo antes que perderlo
            out[col["name"]] = value
        return out

    def _flush(self, commit_msg) -> None:
        """Commit de la tx wal2json: staging + progreso en UNA transacción;
        el ack al slot va SOLO DESPUÉS del commit (§2: pérdida imposible).
        P2-7: heartbeat_at avanza con CADA tx aplicada (misma tx) y también
        con las tx vacías — un flujo continuo de tx filtradas no llega a la
        rama de keepalive del stream y sin esto el latido se estancaría."""
        rows, self._tx = self._tx, []
        flush_lsn = commit_msg.data_start
        if rows:
            with self._core.cursor() as cur:
                cur.execute("BEGIN")
                try:
                    self._insert_changes(cur, rows)
                    cur.execute(
                        "UPDATE shadow_capture_state "
                        "SET last_applied_lsn = %s, updated_at = now(), "
                        "heartbeat_at = now() WHERE id = 1",
                        (flush_lsn,),
                    )
                    cur.execute("COMMIT")
                except Exception:
                    cur.execute("ROLLBACK")
                    raise
            self.rows_applied += len(rows)
            logger.debug(
                "Tx aplicada: %d cambios hasta %s", len(rows), int_to_lsn(flush_lsn)
            )
        else:
            self._touch_heartbeat()
        # Tx vacías (add-tables filtró todo del lado servidor) avanzan el
        # flush igualmente: sin esto el slot retendría WAL ajeno (§8).
        if self._ack:
            self._cur.send_feedback(flush_lsn=flush_lsn)

    def _touch_heartbeat(self) -> None:
        """LATIDO del consumidor (P2-7): UPDATE mínimo (autocommit) de
        heartbeat_at — liveness, SIN tocar last_applied_lsn/updated_at (esos
        son progreso de DATOS). Antes del bootstrap la fila id=1 no existe:
        el UPDATE es un no-op inocuo. Un error de conexión SUBE: run() lo
        trata como caída y reconecta con backoff."""
        with self._core.cursor() as cur:
            cur.execute(
                "UPDATE shadow_capture_state SET heartbeat_at = now() WHERE id = 1"
            )

    # ------------------------------------------------------------ estado/slot

    def _read_state(self) -> dict | None:
        with self._core.cursor() as cur:
            cur.execute(
                "SELECT slot_name, snapshot_lsn, last_applied_lsn "
                "FROM shadow_capture_state WHERE id = 1"
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "slot_name": row[0],
            "snapshot_lsn": row[1],
            "last_applied_lsn": row[2],
        }

    def _slot_exists(self) -> bool:
        with self._core.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_replication_slots WHERE slot_name = %s",
                (self.slot,),
            )
            return cur.fetchone() is not None

    # ------------------------------------------------------------ producción

    def run(self) -> None:
        """Bucle de producción: reconexión con backoff exponencial; los
        errores de configuración/estado (RuntimeError) NO se reintentan —
        suben y el restart del contenedor los hace visibles."""
        backoff = 1.0
        while not self._stop:
            try:
                self.start()
                backoff = 1.0
                self.stream()
            # InterfaceError también es fallo de CONEXIÓN reintentable:
            # psycopg2 lo lanza p.ej. al operar sobre una conexión que murió
            # bajo los pies del stream ("connection already closed") — sin él
            # el contenedor caería en vez de reconectar con backoff.
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                logger.warning(
                    "Conexión perdida (%s); reintento en %.0f s", exc, backoff
                )
                self.close()
                self._sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            finally:
                self.close()
        logger.info("Captura detenida limpiamente")

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            time.sleep(0.5)


def _core_dsn() -> str:
    return os.getenv("CORE_DATABASE_URL", settings.CORE_DATABASE_URL).replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def health_check() -> int:
    """Healthcheck ligero para compose (`--health`): conexión core OK + slot
    presente y ACTIVO (walsender conectado) + LATIDO reciente. Exit 0 sano
    / 1 no.

    Slot presente pero `active=false` = consumidor caído mientras el slot
    retiene WAL de la BD compartida (§8): unhealthy con motivo. El único
    inactivo LEGÍTIMO es el backfill del bootstrap (slot ya creado, aún sin
    START_REPLICATION) — lo cubre el start_period de 300s del compose.

    LIVENESS por `heartbeat_at` (P2-7, rev. externa parte 2): el consumidor
    lo actualiza en cada keepalive del stream (~status_interval, 10 s) y en
    cada tx aplicada — antes se medía `updated_at`, que SOLO avanza con txs
    capturadas: con slot activo y días sin tráfico legacy el healthcheck
    daba FALSO unhealthy (la evidencia del revisor: 3 días sin tráfico).
    `last_applied_lsn`/`updated_at` quedan como progreso de DATOS (se
    reporta, no se puntúa). Umbral: el ACTUAL (26 h por defecto,
    CORE_CAPTURE_HEALTH_MAX_AGE_S) — un consumidor vivo lo satisface de
    sobra con latido cada 10 s. COALESCE a updated_at: estado anterior a
    core0009 sin latido registrado.
    """
    slot = os.getenv("CORE_CAPTURE_SLOT", DEFAULT_SLOT)
    max_age = float(os.getenv("CORE_CAPTURE_HEALTH_MAX_AGE_S", str(26 * 3600)))
    try:
        conn = psycopg2.connect(
            _core_dsn(),
            options=f"-c search_path={settings.CORE_DB_SCHEMA},public",
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active FROM pg_replication_slots WHERE slot_name = %s",
                    (slot,),
                )
                slot_row = cur.fetchone()
                if slot_row is None:
                    print(f"unhealthy: slot {slot} ausente", file=sys.stderr)
                    return 1
                if not slot_row[0]:
                    print(
                        f"unhealthy: slot {slot} presente pero INACTIVO "
                        "(consumidor no conectado; el slot retiene WAL)",
                        file=sys.stderr,
                    )
                    return 1
                cur.execute(
                    "SELECT last_applied_lsn, "
                    "extract(epoch FROM now() - COALESCE(heartbeat_at, "
                    "updated_at)) "
                    "FROM shadow_capture_state WHERE id = 1"
                )
                row = cur.fetchone()
                if row is None:
                    print(
                        "unhealthy: sin frontera registrada (bootstrap incompleto)",
                        file=sys.stderr,
                    )
                    return 1
                last_applied, age = row
                if age is None or float(age) > max_age:
                    print(
                        f"unhealthy: sin LATIDO del consumidor (edad={age}s > "
                        f"{max_age:.0f}s; progreso de datos: "
                        f"last_applied={int_to_lsn(last_applied)})",
                        file=sys.stderr,
                    )
                    return 1
        finally:
            conn.close()
    except Exception as exc:  # cualquier fallo = unhealthy con motivo visible
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    if "--health" in sys.argv[1:]:
        raise SystemExit(health_check())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    core_dsn = _core_dsn()
    capture_dsn = os.getenv(
        "CAPTURE_DSN",
        "postgresql://jobhunt_capture:jobhunt_capture_dev@postgres:5432/swissjobhunter",
    )
    capture = ShadowCapture(
        capture_dsn,
        core_dsn,
        slot=os.getenv("CORE_CAPTURE_SLOT", DEFAULT_SLOT),
        tables=os.getenv("CORE_CAPTURE_TABLES", DEFAULT_TABLES),
    )
    signal.signal(signal.SIGTERM, lambda *_: capture.stop())
    signal.signal(signal.SIGINT, lambda *_: capture.stop())
    capture.run()


if __name__ == "__main__":
    main()
