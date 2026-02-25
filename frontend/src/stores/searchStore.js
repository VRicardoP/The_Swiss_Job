import { create } from "zustand";

const initialFilters = {
  q: "",
  source: "",
  canton: "",
  language: "",
  seniority: "",
  contractType: "",
  remoteOnly: false,
  salaryMin: "",
  salaryMax: "",
  sort: "newest",
};

export const useSearchStore = create((set) => ({
  ...initialFilters,
  filtersOpen: false,

  setQ: (q) => set({ q }),
  setSource: (source) => set({ source }),
  setCanton: (canton) => set({ canton }),
  setLanguage: (language) => set({ language }),
  setSeniority: (seniority) => set({ seniority }),
  setContractType: (contractType) => set({ contractType }),
  setRemoteOnly: (remoteOnly) => set({ remoteOnly }),
  setSalaryMin: (salaryMin) => set({ salaryMin }),
  setSalaryMax: (salaryMax) => set({ salaryMax }),
  setSort: (sort) => set({ sort }),
  setFiltersOpen: (filtersOpen) => set({ filtersOpen }),

  resetFilters: () => set(initialFilters),
}));
