#!/bin/sh
# Regression ONLY for empty, network-isolated, disposable PostgreSQL containers.
# With PID 1 postgres, this unrelated child exit induces crash recovery;
# with Docker --init the init process reaps it, leaving PostgreSQL undisturbed.
# Never invoke inside a real database container, including restored private copies.
case "${1-}" in
  swissjob-init-proof-before-20260920|swissjob-init-proof-after-20260920) ;;
  *) exit 64 ;;
esac
[ "${POSTGRES_DB-}" = probe ] && [ "${POSTGRES_USER-}" = probe ] || exit 64
sh -c 'sleep 1; exit 2' </dev/null >/dev/null 2>&1 &
