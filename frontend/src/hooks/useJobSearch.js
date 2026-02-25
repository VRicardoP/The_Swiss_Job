import { useInfiniteQuery } from "@tanstack/react-query";
import { jobsApi } from "../config/api";
import { useSearchStore } from "../stores/searchStore";

export function useJobSearch() {
  const {
    q,
    source,
    canton,
    language,
    seniority,
    contractType,
    remoteOnly,
    salaryMin,
    salaryMax,
    sort,
  } = useSearchStore();

  const params = {
    q: q || undefined,
    source: source || undefined,
    canton: canton || undefined,
    language: language || undefined,
    seniority: seniority || undefined,
    contract_type: contractType || undefined,
    remote_only: remoteOnly || undefined,
    salary_min: salaryMin || undefined,
    salary_max: salaryMax || undefined,
    sort,
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
