import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyticsApi, matchApi } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('Exclusion API responses', () => {
  it('accepts a successful DELETE with no JSON body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    await expect(analyticsApi.deleteFilter('test')).resolves.toBeUndefined()
  })

  it('preserves pending status on reads', async () => {
    const payload = { data: [], sync_status: { pending: true, version: 2 } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(payload)))
    await expect(analyticsApi.listFilters()).resolves.toEqual(payload)
  })

  it('does not swallow a failed deletion', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ detail: 'offline' }, { status: 503 })))
    await expect(analyticsApi.deleteFilter('test')).rejects.toMatchObject({ status: 503 })
  })
})


describe('Match facets API', () => {
  it('requests a bounded category page without title translation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({ data: [], total: 0, weights_used: {} }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await matchApi.getResults({
      limit: 50,
      offset: 100,
      category: 'H',
      translate: false,
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v1/match/results?limit=50&offset=100&category=H&translate=false',
    )
  })
})
