#!/bin/bash
# QNAP project backups only. Install via the authenticated administrative scheduler.
# Never mount the Docker socket in a container; expiry gets only the private root.
set -euo pipefail
umask 077
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
R=/share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88
OPS="$R/e13-erasure"
test -d "$OPS"
# Cron must invoke this as Ricardo, not root; retain private user ownership.
test "$(id -u)" = 1000
test "$(stat -c %a "$R")" = 700
test "$(stat -c %a "$OPS")" = 700
# A stale lock fails visibly. Inspect the recorded PID before manual recovery.
mkdir "$OPS/backup-running" || { echo 'BACKUP_BLOCKED: lock exists'; exit 2; }
printf '%s\n' "$$" > "$OPS/backup-running/pid"
trap 'rm -f "$OPS/backup-running/pid"; rmdir "$OPS/backup-running"' EXIT
stamp=$(date -u +%Y%m%dT%H%M%SZ)
batch="$OPS/daily-$stamp"
mkdir "$batch"
retention() {
  "$D" run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --user "$(id -u):$(id -g)" \
    -v "$R:$R" -v "$OPS/backup_retention.py:/tool.py:ro" \
    --entrypoint python swissjob-core:d2a38e9 /tool.py "$OPS/retention.json" "$@"
}
backup() {
  container=$1 db=$2 role=$3
  archive="$batch/$db.dump"
  created=$(date +%s)
  if ! "$D" exec "$container" pg_dump -U "$role" -d "$db" -Fc -Z1 > "$archive.partial"; then
    retention --register "$archive.partial" --kind temporary --database "$db" --created-at "$created"
    return 1
  fi
  if ! "$D" exec -i "$container" pg_restore --list < "$archive.partial" > "$archive.toc"; then
    retention --register "$archive.partial" --kind temporary --database "$db" --created-at "$created"
    return 1
  fi
  mv "$archive.partial" "$archive"
  # Archive readability is checked per run. A full isolated restore remains
  # mandatory before production restoration, not implied by pg_restore --list.
  retention --register "$archive" --kind backup --database "$db" \
    --created-at "$created" --verified
}
backup swissjob-postgres swissjobhunter swissjob
backup swissjob-postgres swissjobhunter_r5_rehearsal swissjob
backup portfolio_db proyecto lothar
# Minimal suppression inventory is independent of old dumps, and regenerated
# from the live authority. Keep snapshots: never expire them as ordinary backups.
"$D" exec swissjob-core-api-r5 python -c \
 'import asyncio,json; from jobhunt_core.database import task_session_factory; from jobhunt_core.erasure_restore import snapshot
async def main():
 async with task_session_factory() as f:
  async with f() as s: print(json.dumps(await snapshot(s)))
asyncio.run(main())' > "$batch/erasures.json.partial"
test -s "$batch/erasures.json.partial"
mv "$batch/erasures.json.partial" "$batch/erasures.json"
retention --apply > "$batch/retention-result.json"
sync
date -u +%FT%TZ > "$OPS/last-backup-success"
sync
echo 'BACKUP_OK: archives readable, retention checked; isolated restore still mandatory'
