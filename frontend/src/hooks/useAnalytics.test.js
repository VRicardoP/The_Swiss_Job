import { expect, it, vi } from 'vitest'
import { useFilters } from './useAnalytics'

vi.mock('@tanstack/react-query', () => ({
  useQuery: options => options,
  useMutation: options => options,
  useQueryClient: () => ({}),
}))

it('polls only while exclusion delivery is pending', () => {
  const { refetchInterval } = useFilters()
  expect(refetchInterval({ state: { data: { sync_status: { pending: true } } } })).toBe(5000)
  expect(refetchInterval({ state: { data: { sync_status: { pending: false } } } })).toBe(false)
  expect(refetchInterval({ state: {} })).toBe(false)
})
