import { useEffect, useState } from "react";
import { useSearchStore } from "../stores/searchStore";
import { jobsApi } from "../config/api";

const CANTONS = [
  "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR",
  "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG",
  "TI", "UR", "VD", "VS", "ZG", "ZH",
];

const LANGUAGES = ["de", "fr", "en", "it"];

const SENIORITY = ["intern", "junior", "mid", "senior", "lead", "head"];

const CONTRACT_TYPES = [
  "full_time", "part_time", "contract", "freelance", "temporary", "apprenticeship",
];

export default function FilterPanel() {
  const {
    source, canton, language, seniority, contractType,
    remoteOnly, salaryMin, salaryMax,
    setSource, setCanton, setLanguage, setSeniority, setContractType,
    setRemoteOnly, setSalaryMin, setSalaryMax,
    filtersOpen, setFiltersOpen, resetFilters,
  } = useSearchStore();

  const [sources, setSources] = useState([]);

  useEffect(() => {
    jobsApi.getSources().then((data) => {
      setSources(data.map((s) => s.name));
    }).catch(() => {});
  }, []);

  if (!filtersOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 backdrop-blur-sm z-40"
        onClick={() => setFiltersOpen(false)}
      />

      {/* Panel */}
      <div className="fixed inset-x-0 bottom-0 z-50 bg-surface rounded-t-3xl shadow-modal animate-slide-up max-h-[80vh] overflow-y-auto">
        <div className="p-4">
          {/* Handle */}
          <div className="w-12 h-1.5 bg-text-tertiary rounded-full mx-auto mb-4" />

          <h2 className="text-xl font-bold text-text-primary mb-4">Filters</h2>

          <div className="space-y-4">
            {/* Source */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Source</label>
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
              >
                <option value="">All sources</option>
                {sources.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>

            {/* Canton */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Canton</label>
              <select
                value={canton}
                onChange={(e) => setCanton(e.target.value)}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
              >
                <option value="">All cantons</option>
                {CANTONS.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>

            {/* Language */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Language</label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
              >
                <option value="">All languages</option>
                {LANGUAGES.map((l) => (
                  <option key={l} value={l}>{l.toUpperCase()}</option>
                ))}
              </select>
            </div>

            {/* Seniority */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Seniority</label>
              <select
                value={seniority}
                onChange={(e) => setSeniority(e.target.value)}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
              >
                <option value="">All levels</option>
                {SENIORITY.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>

            {/* Contract Type */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Contract</label>
              <select
                value={contractType}
                onChange={(e) => setContractType(e.target.value)}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
              >
                <option value="">All types</option>
                {CONTRACT_TYPES.map((t) => (
                  <option key={t} value={t}>{t.replace("_", " ")}</option>
                ))}
              </select>
            </div>

            {/* Remote */}
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={remoteOnly}
                onChange={(e) => setRemoteOnly(e.target.checked)}
                className="rounded border-border accent-swiss-red"
              />
              <span className="text-sm text-text-secondary">Remote only</span>
            </label>

            {/* Salary range */}
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Min salary (CHF)</label>
                <input
                  type="number"
                  value={salaryMin}
                  onChange={(e) => setSalaryMin(e.target.value)}
                  placeholder="0"
                  className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
                />
              </div>
              <div className="flex-1">
                <label className="block text-xs font-semibold uppercase tracking-wider text-text-tertiary mb-1">Max salary (CHF)</label>
                <input
                  type="number"
                  value={salaryMax}
                  onChange={(e) => setSalaryMax(e.target.value)}
                  placeholder="300000"
                  className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
                />
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3 mt-6 pb-2">
            <button
              onClick={() => { resetFilters(); setFiltersOpen(false); }}
              className="flex-1 rounded-xl border border-border px-4 py-2.5 text-sm font-medium text-text-secondary hover:bg-surface-tertiary transition-colors duration-200"
            >
              Reset
            </button>
            <button
              onClick={() => setFiltersOpen(false)}
              className="flex-1 rounded-xl bg-swiss-red hover:bg-swiss-red-hover px-4 py-2.5 text-sm font-semibold text-white transition-all duration-200"
            >
              Apply
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
