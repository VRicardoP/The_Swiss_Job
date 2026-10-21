import { create } from "zustand";

// DT-148 A18-01 (2026-10-21): el store modelaba `canton`, `language`,
// `seniority`, `contractType`, `salaryMin`, `salaryMax` y cuatro órdenes. El
// catálogo del núcleo sirve exactamente q, source, remote y location, así que
// los nueve restantes acababan en 501 — un usuario que filtraba por cantón
// recibía un error. No se implementaron en el `/v1` porque la medición lo
// desaconseja: sobre 5.000 vacantes activas la cobertura va del 9,8 % de
// `canton` al 0 % de `seniority`. `location` los sustituye y sí funciona: el
// núcleo lo filtra como substring sobre un campo presente en el 100 % de las
// canónicas.
const initialFilters = {
  q: "",
  source: "",
  location: "",
  remoteOnly: false,
};

export const useSearchStore = create((set) => ({
  ...initialFilters,
  filtersOpen: false,

  setQ: (q) => set({ q }),
  setSource: (source) => set({ source }),
  setLocation: (location) => set({ location }),
  setRemoteOnly: (remoteOnly) => set({ remoteOnly }),
  setFiltersOpen: (filtersOpen) => set({ filtersOpen }),

  resetFilters: () => set(initialFilters),
}));
