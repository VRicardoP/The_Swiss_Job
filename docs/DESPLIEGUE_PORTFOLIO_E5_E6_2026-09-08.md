# Portfolio E.5/E.6 — restore autorizado y backend desplegado (2026-09-08)

## Resultado comprobado

Backend NAS **`portfolio-backend:e6-ddb6651`**, código Portfolio **`ddb6651`**,
Alembic **`rr11s0042u18`**, activo y sin reinicios en la comprobación inicial.
Conservación documental E.5 desplegada; journal E.6 instalado pero **NO conectado
todavía a generador/UI/scheduler ni activado como escritor documental core**.
SwissJob/core/modelos/holdout no se modificaron. E/F NO se declaran completas.

- HTTP autenticado: `/health/deep`, `/api/v1/cv-generation/` y
  `/api/v1/applications/` → **200**. Ambos listados estaban vacíos; esto NO es
  un canary de generación LLM ni de PDF con documentos reales.
- Base y Redis: healthy. FK de documento hacia candidatura: **0**; se conserva
  el vínculo de propietario. Binding explícito user 1 ↔ perfil enrolado comprobado.
- Huellas post-despliegue de `cv_profiles`, `schools`, `school_jobs` y
  `saved_searches`: **idénticas** a la copia inicial. Usuarios: sigue 1;
  documentos y journal: 0. Comparación por hashes/conteos sin publicar contenido.
  No se deduce de ello que no puedan existir cambios futuros legítimos.
- Proxy: `unsafe=[]`, `rejected=[]`, limitación por cliente y entrypoint vigente.
- Túnel público `/health`: **200 / healthy** tras la corrección y tras el relevo.
- Frontend **`8836254`** publicado en GitHub por fast-forward desde `97092ae`;
  no se sobreescribió ninguna rama divergente. **Cloudflare Pages y tests CI:
  completed/success**, commit exacto 8836254. Dominio público sirve
  `SelectedOffersPanel-CIkAngwv.js` con `document-library`,
  `dashboard.cvGeneration.library` y `dashboard.cvGeneration.retentionNotice`;
  HTML con API ngrok correcto. Se verificó el asset real, no solo la preview
  ni el estado del build. No se usó sesión de navegador del propietario.

## Copia y ensayo real

El propietario autorizó explícitamente copiar `proyecto` desde su NAS, incluidos
datos personales y hashes de autenticación, a una carpeta privada local para
restore/migración/rollback. Ese bloqueo de §29 queda **RESUELTO**, no volver a pedirlo
como si la autorización no existiera. No se publicaron datos ni credenciales.

- Copia local privada (directorio 0700, dump 0600):
  `/tmp/portfolio-authorized-restore.00CJg58N/portfolio.dump`.
- SHA-256: `9ff0421738044dd9002ec3efe624aaf5319a30ee5d8b40b2e595646118ffd886`.
- Restore con `--exit-on-error --single-transaction --no-owner --no-acl`, log
  conservado, sin red a servicios externos desde la aplicación de ensayo.
- PG NAS 15.15; se repitió con **15.15 exacto** tras un primer restore en 15.16.
- Inventario restaurado: **148 columnas, 24 constraints, 51 índices, 0 triggers
  de usuario**, revisión `ll55m4480o12`. Ningún constraint desapareció.
- `pg_restore` reformula un único CHECK de routing (cast del array frente a casts
  por elemento), incluso con 15.15 exacto. Misma lista de cinco modos, misma
  validación; tabla de verdad comparada contra NAS para cinco válidos, tres
  inválidos y NULL. NO se presenta como igualdad textual de esos dos SQL.
- Ida y vuelta final de la copia: **igualdad exacta** de huellas de datos,
  secuencias, columnas, constraints, índices, triggers y revisión contra su
  baseline restaurada. Las evidencias JSON solo contienen metadatos y hashes.

Los contenedores locales de ensayo se retiraron al terminar; sus copias derivadas
son recuperables desde el dump privado conservado. El dump no está en Git.

## Fallos que el ensayo evitó desplegar

1. **Deriva real create_all/Alembic:** NAS ya tenía los cuatro índices que `oo88`
   intenta crear. `alembic upgrade head` falló con DuplicateTable y revirtió.
   Reproducción sintética roja antes del fix; no era un problema imaginado.
   `services.schema_upgrade.upgrade_to_head` valida SOLO definiciones exactas de
   esos índices no únicos/sin constraint, las prepara y migra en UNA transacción.
   Definición distinta o fallo posterior → rollback íntegro. Locks con timeout 5 s.
   No se reescribieron migraciones publicadas ni se hizo stamp manual.
2. **Downgrade no reconstruía la deriva original:** dos índices redundantes del
   NAS eran UNIQUE; el downgrade publicado los recrea no únicos. El ensayo repuso
   las seis definiciones originales afectadas, dentro de la vuelta. No se afirma
   que un `alembic downgrade` a secas sea un rollback exacto de este NAS.
3. **Regresión del nuevo invocador programático:** `fileConfig` desactivaba los
   loggers del proceso llamador. La suite detectó cuatro comprobaciones de logs
   rotas. Con conexión suministrada Alembic conserva el logging del llamador;
   nueva regresión comprueba loggers y handlers, sin debilitar los tests anteriores.
4. **Proxy ya roto antes del relevo:** ngrok apuntaba a `http://backend:8000` y
   devolvía 502; Portfolio carecía de ese alias en `portfolio_default`. Se añadió
   el alias manteniendo IP 172.29.4.5. En la otra red `backend` resolvía SwissJob:
   no se reutilizó ni modificó ese alias ajeno.

Incidentes del entorno de ensayo, no defectos productivos: `DEBUG=release`
heredado del terminal impidió un primer Alembic antes de DDL; el primer arranque
sintético omitió una SECRET_KEY válida y fue rechazado correctamente. Se corrigió
la configuración de ensayo, no las guardas de producción.

## Verificación y release

- Regresión roja NAS: DuplicateTable del mismo índice que la copia real.
- Cuatro tests PG del invocador: deriva conocida, deriva inesperada, fallo
  posterior con restauración íntegra y base vacía/segunda ejecución.
- Regresión de logging + scrapers afectados: **54 passed**.
- Suite final Portfolio: **1928 passed, 1 skipped**, 189,05 s, en serie.
  Una pasada previa dio 4 failed por logging; no se oculta ni se cuenta como verde.
- Imagen construida desde `git archive ddb6651`, contexto separado del backup,
  sin red de build ni cambios de dependencias. 113 paquetes Python y paquetes
  de sistema con huellas iguales a la base del contenedor NAS anterior.
- La imagen, no solo el host venv, migró la copia y arrancó con health 200.
- Imagen local manifest: `sha256:7fa3ab2d4ccf5cbd980196480f7a07b00c14ae7c3ef6866dfabdd9b44d877c77`.
- Tar comprimido transferido: SHA-256
  `629b96e66f1cade7d1a0914259baa32e65c73857ec12140d39ae250c54682264`,
  igual en local y NAS. Se hizo docker save LOCALMENTE, nunca en NAS.

## Configuración, rollback y seguridad operativa

- Puerto 8002→8000, redes `portfolio_default` y `swissjob_swissjob-net`, IPs
  172.29.4.5 y 172.29.16.10 preservadas. Alias `backend` solo en la primera.
- `TRUSTED_PROXIES`: loopback y **172.29.4.4**, IP comprobada de portfolio_ngrok;
  fuera el rango 172.29.0.0/16. Hosts limitados a los nombres/IP usados y al
  dominio ngrok real; se retiró `allowed_hosts=["*"]`.
- Configuración/credenciales conservadas dentro del NAS, sin copiarlas al informe.
  Carpeta operativa 0700 y env/dump 0600:
  `/share/CACHEDEV1_DATA/Public/portfolio-release-e6.XXCDe1Ax/`.
  Contiene preflight, script dirigido, configuración anterior, nuevo env, logs y
  **pre-cutover.dump tomado después de drenar el backend**.
- Caché Hugging Face del contenedor anterior conservada y montada en el nuevo:
  no se pierde el modelo descargado ni se fuerza otro frío por cambiar imagen.
- Contenedor anterior **`portfolio_backend_pre_ddb6651`**, parado y retenido.
  Reversión de imagen: script privado `nas_release.sh rollback`. No sobrescribe
  BD, no elimina documentos nuevos, no ejecuta downgrade destructivo. Su rama
  automática no fue necesaria en este despliegue; no se presenta como ensayo vivo
  de rollback después de escrituras nuevas.
- El antiguo `portfolio_backend_pre_flip` ya existía; no se tocó ni eliminó.

## Pendiente y deuda explícita

- Schedulers **siguen ON**, como estaban; logs confirmaron cosecha legacy al
  arrancar. Este relevo preserva ese comportamiento, **NO certifica quiesce ni
  cierre de escritores legacy**. Inventariar qué sustituye core y qué sigue
  atendiendo colegios/alertas antes de apagar selectivamente. No activar un
  segundo escritor de durables al resolverlo.
- IP del proxy concreta, pero mantenerla si se recrea ngrok o actualizar el
  binding verificando su nueva IP. No volver a confiar en todo el bridge.
- E documental: falta conectar journal al generador/UI/retry, autoridad de
  lectura/PDF/retención/inbox y borrado de dueño; migración histórica y rollback
  con escrituras nuevas antes del flip. Datos locales cero NO eximen de probarlo.
- Colegios y retirada F siguen abiertos. API/código local core0043 no se ha
  desplegado al core por actualizar únicamente Portfolio.
- GO de calidad separado: holdout independiente nuevo; no se cambian métricas,
  etiquetas, modelos ni racha. No declarar unificación completa por este despliegue.
