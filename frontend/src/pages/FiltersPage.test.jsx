import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import FiltersPage from './FiltersPage'

const state = vi.hoisted(() => ({ data: { data: [] } }))
vi.mock('../hooks/useAnalytics', () => ({
  useFilters: () => ({ data: state.data }),
  useSuggestions: () => ({ data: { data: [] } }),
  useAnalyzeSuggestions: () => ({ mutate: () => {} }),
  useReviewSuggestion: () => ({ mutate: () => {} }),
  useDeleteFilter: () => ({ mutate: () => {} }),
  useCreateFilter: () => ({ mutate: () => {} }),
}))

describe('Pending exclusions', () => {
  it('shows pending even after the last rule was deleted', () => {
    state.data = { data: [], sync_status: { pending: true } }
    expect(renderToStaticMarkup(<FiltersPage />)).toContain('Synchronization with matching is pending')
  })

  it('removes the notice once delivery is acknowledged', () => {
    state.data = { data: [], sync_status: { pending: false } }
    expect(renderToStaticMarkup(<FiltersPage />)).not.toContain('Synchronization with matching is pending')
  })
})
