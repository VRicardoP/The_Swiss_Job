# Topología real de producción (NAS) — versionada sin secretos

`core.configured.yml` y `swissjob.configured.yml` son **copias literales** de los
composes que Container Station ejecuta en el NAS
(`/share/CACHEDEV1_DATA/Public/unification-e15-20260914/`), con cada valor
sensible o identificador personal sustituido por `${VARIABLE}`. El del Portfolio
vive en su propio repositorio (`ReactPortfolio/backend/deploy/nas/`).

Hasta el 2026-09-28 (A19-04) esta topología **no estaba en ningún repositorio**:
la única copia era la que corría. Cualquier deriva entre lo que se creía
desplegado y lo desplegado sólo se descubría al desplegar (pasó con A19-21).

## Cómo se usan

No se ejecutan tal cual. Para reproducir el fichero real se rellena un `.env.nas`
(NO versionado, `chmod 600`) con las variables que cada fichero declara, y se
renderiza:

```bash
docker compose --env-file .env.nas -f deploy/nas/core.configured.yml config
```

Variables que exige cada fichero: `grep -oE '\$\{[A-Z_]+\}' deploy/nas/*.yml | sort -u`.

## Cómo se mantienen honestas

- `scripts/check_no_secrets.py` falla si cualquier clave con nombre de secreto
  lleva un literal, o si aparece un literal largo con pinta de token. Corre en CI
  (`compose-config`). Control negativo ejecutado el 2026-09-28: una contraseña
  literal de 28 caracteres lo pone en rojo.
- Tras cada despliegue que toque el compose del NAS, se vuelve a copiar aquí y se
  sustituyen los valores: `scripts/deploy_nas.sh` lo recuerda al terminar.

## Qué NO es esto

No es el `docker-compose.qnap.yml` del repo: aquél es la fuente de la que se
generaron estos, y desde entonces han divergido (etiquetas de imagen por
servicio, redes con nombre de proyecto, límites). Este directorio es **lo que
corre**; `qnap.yml` es **lo que se diseñó**. Cuando difieran, manda éste.
