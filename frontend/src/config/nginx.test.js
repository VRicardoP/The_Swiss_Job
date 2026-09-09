// @vitest-environment node
import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'

it('pins production and rehearsal to distinct backend names on shared networks', () => {
  const root = new URL('../../', import.meta.url)
  const nginx = readFileSync(new URL('nginx.conf', root), 'utf8')
  const dockerfile = readFileSync(new URL('Dockerfile.prod', root), 'utf8')
  const rehearsal = readFileSync(new URL('../docker-compose.rehearsal.qnap.yml', root), 'utf8')
  expect(nginx).toContain('proxy_pass http://${SWISSJOB_BACKEND_HOST}:8000;')
  expect(dockerfile).toContain('ENV SWISSJOB_BACKEND_HOST=swissjob-backend')
  expect(dockerfile).toContain('COPY nginx.conf /etc/nginx/templates/default.conf.template')
  expect(rehearsal.split('\n  frontend:')[1]).toContain('SWISSJOB_BACKEND_HOST: swissjob-backend-r5')
})
