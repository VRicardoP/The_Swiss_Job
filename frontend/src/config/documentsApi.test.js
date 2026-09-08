import { afterEach, expect, it, vi } from 'vitest'
import { documentsApi } from './api'

afterEach(() => vi.unstubAllGlobals())

it('reuses the caller operation and preserves an accepted pending result', async () => {
  const payload = { status: 'pending', operation_id: 'same-operation', error: 'delivery_failed' }
  const fetch = vi.fn().mockImplementation(async () => Response.json(payload, { status: 202 }))
  vi.stubGlobal('fetch', fetch)
  await expect(documentsApi.generate('job', 'cv', 'fr', 'same-operation')).resolves.toEqual(payload)
  await documentsApi.generate('job', 'cv', 'fr', 'same-operation')
  expect(fetch.mock.calls.map(([, options]) => JSON.parse(options.body).operation_id))
    .toEqual(['same-operation', 'same-operation'])
})

it('encodes a library cursor and fetches the authoritative document by ID', async () => {
  const fetch = vi.fn().mockImplementation(async () => Response.json({ data: [], next_cursor: null }))
  vi.stubGlobal('fetch', fetch)
  await documentsApi.page('a+/=')
  await documentsApi.get('document-id')
  expect(fetch.mock.calls[0][0]).toContain('/documents?cursor=a%2B%2F%3D')
  expect(fetch.mock.calls[1][0]).toContain('/documents/item/document-id')
})


it('accepts a document DELETE with an empty 204 response', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
  await expect(documentsApi.remove('document-id')).resolves.toBeUndefined()
})
