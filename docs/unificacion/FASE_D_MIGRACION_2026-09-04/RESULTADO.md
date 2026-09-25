# Fase D — migración de durables EJECUTADA (2026-09-04)

Runbook `RUNBOOK_CUTOVER_FASE_D.md`; migrador `import_swissjob_durables.py`
(SwissJob `2cee42d`, 3/3 tests). Orden cumplido: backups frescos → restore
PROBADO sobre copia (RTO medidos: legacy 535 s, core 1.296 s; backups 327/588 s)
→ ENSAYO completo sobre la copia (migración + rollback byte-equivalente +
re-migración idempotente) → ventana real con huella material estable
(pre==post: `6ae12d18b5690dd2…`).

## Resultado en el core productivo

- feedback: 18/18 (14 thumbs_down TODOS con dismissed_at; 4 thumbs_up sin él)
- saved_searches: 10/10 (4 fix-ups de notify weekly/push reales)
- exclusiones P2: 7 patrones → `exclude_title_contains` en su búsqueda
- 0 unresolved · 0 inválidos · estado preexistente intacto (2 filas con
  puntero de feed detectadas y respetadas en el ensayo)

Manifiesto de procedencia por VALORES (rollback exacto posible):
`manifiesto_fase_d_real.json`.

## sha256

```
4ffcc49323c95fb4979192ad035d6023e294c17e73a319c1fc351e387af760e5  plan_fase_d.json
fbf1c63a47c732815bd7bcdc2e7baa9e09d9c8abed25135164f1c00206169234  manifiesto_fase_d_real.json
e68c8b832f6e6fc9434e3136dd9e4ad02247f5d0d69f10f07ab46fa9c552ad9d  backup_fase_d.log
```
