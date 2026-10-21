import { useInfiniteQuery } from "@tanstack/react-query";
import { jobsApi } from "../config/api";
import { useSearchStore } from "../stores/searchStore";

export function useJobSearch() {
  const { q, source, location, remoteOnly } = useSearchStore();

  // DT-148 A18-01: sólo viaja lo que el catálogo sirve. `sort` tampoco: el
  // `/v1` ordena por `newest` y era el único valor que no daba 501.
  const params = {
    q: q || undefined,
    source: source || undefined,
    location: location || undefined,
    remote_only: remoteOnly || undefined,
  };

  return useInfiniteQuery({
    queryKey: ["jobs", params],
    queryFn: ({ pageParam = 0 }) =>
      jobsApi.search({ ...params, offset: pageParam, limit: 20 }),
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? lastPage.offset + lastPage.limit : undefined,
    initialPageParam: 0,
  });
}
