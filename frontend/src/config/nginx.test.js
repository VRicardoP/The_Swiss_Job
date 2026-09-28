// @vitest-environment node
import { existsSync, readFileSync } from 'node:fs'
import { expect, it } from 'vitest'

const root = new URL('../../', import.meta.url)
const rehearsalPath = new URL('../docker-compose.rehearsal.qnap.yml', root)

// A20-14: el compose de ensayo vive en la RAÍZ del repo; dentro del contenedor
// `frontend` (que solo monta frontend/) no existe y el test fallaba por
// fichero ausente, no por lo que mide. Se salta con motivo en vez de fallar.
it.skipIf(!existsSync(rehearsalPath))(
  'pins production and rehearsal to distinct backend names on shared networks',
  () => {
  const nginx = readFileSync(new URL('nginx.conf', root), 'utf8')
  const dockerfile = readFileSync(new URL('Dockerfile.prod', root), 'utf8')
  const rehearsal = readFileSync(rehearsalPath, 'utf8')
  expect(nginx).toContain('proxy_pass http://${SWISSJOB_BACKEND_HOST}:8000;')
  expect(dockerfile).toContain('ENV SWISSJOB_BACKEND_HOST=swissjob-backend')
  expect(dockerfile).toContain('COPY nginx.conf /etc/nginx/templates/default.conf.template')
  expect(rehearsal.split('\n  frontend:')[1]).toContain('SWISSJOB_BACKEND_HOST: swissjob-backend-r5')
  },
)
