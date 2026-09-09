import { afterEach, expect, it, vi } from 'vitest'
import { documentsApi } from './api'

afterEach(() => vi.unstubAllGlobals())

it('closes an acknowledged operation whose document was subsequently deleted without regenerating', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(Response.json({ status: 'delivered', operation_id: 'op', document_id: 'doc' }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ detail: 'Document not found' }, { status: 404 }))
  vi.stubGlobal('fetch', fetch)
  await expect(documentsApi.generateAndFetch('job', 'cv', 'en', 'op'))
    .resolves.toMatchObject({ status: 'removed', operation_id: 'op', document_id: 'doc' })
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls.filter(([, opts]) => opts.method === 'POST')).toHaveLength(1)
})

it.each([401, 403, 503])('does not treat HTTP %s as deletion', async (status) => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(Response.json({ status: 'delivered', document_id: 'doc' }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ detail: 'Unavailable' }, { status }))
  vi.stubGlobal('fetch', fetch)
  await expect(documentsApi.generateAndFetch('job', 'cv', 'en', 'op'))
    .rejects.toMatchObject({ status })
  expect(fetch).toHaveBeenCalledTimes(2)
})
