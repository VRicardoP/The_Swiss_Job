import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { analyticsApi } from '../config/api'

export function useAnalyzeSuggestions() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (params) => analyticsApi.analyze(params),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['analytics-suggestions'] })
    },
  })
}

export function useSuggestions(statusFilter = 'pending') {
  return useQuery({
    queryKey: ['analytics-suggestions', statusFilter],
    queryFn: () => analyticsApi.listSuggestions(statusFilter),
    staleTime: 30_000,
  })
}

export function useReviewSuggestion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, action }) => analyticsApi.reviewSuggestion(id, action),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['analytics-suggestions'] })
      qc.invalidateQueries({ queryKey: ['analytics-filters'] })
    },
  })
}

export function useFilters() {
  return useQuery({
    queryKey: ['analytics-filters'],
    queryFn: () => analyticsApi.listFilters(),
    staleTime: 30_000,
  })
}

export function useDeleteFilter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id) => analyticsApi.deleteFilter(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['analytics-filters'] })
    },
  })
}

export function useCreateFilter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => analyticsApi.createFilter(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['analytics-filters'] })
    },
  })
}
