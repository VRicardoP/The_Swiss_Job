import { useEffect, useState } from "react";
import { X, RotateCcw } from "lucide-react";
import { useSearchStore } from "../stores/searchStore";
import { jobsApi } from "../config/api";
import { Button, Select, Input, IconButton, cn } from "./ui";

const CANTONS = [
  "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR",
  "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG",
  "TI", "UR", "VD", "VS", "ZG", "ZH",
];
const LANGUAGES = ["de", "fr", "en", "it"];
const SENIORITY = ["mid", "senior", "lead", "head", "director"];
const CONTRACT_TYPES = ["full_time", "part_time", "contract", "temporary"];

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
    jobsApi
      .getSources()
      .then((data) => setSources(data.map((s) => s.name)))
      .catch(() => {});
  }, []);

  // Cerrar con Escape
  useEffect(() => {
    if (!filtersOpen) return;
    function onKey(e) {
      if (e.key === "Escape") setFiltersOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [filtersOpen, setFiltersOpen]);

  if (!filtersOpen) return null;

  return (
    <>
      <button
        type="button"
        aria-label="Close filters"
        className="fixed inset-0 z-40 bg-ink/40 backdrop-blur-[2px] animate-fade-in"
        onClick={() => setFiltersOpen(false)}
      />

      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Filters"
        className={cn(
          "fixed z-50 bg-surface shadow-modal",
          // Mobile: bottom sheet
          "inset-x-0 bottom-0 max-h-[85vh] rounded-t-2xl animate-slide-up",
          // Desktop: side sheet
          "sm:inset-y-0 sm:right-0 sm:left-auto sm:bottom-auto sm:top-0 sm:h-full sm:max-h-none sm:w-[420px] sm:rounded-none sm:border-l sm:border-border sm:animate-slide-down",
          "flex flex-col overflow-hidden",
        )}
      >
        {/* Mobile handle */}
        <div className="flex justify-center pt-3 sm:hidden">
          <div className="h-1 w-10 rounded-full bg-border-strong" />
        </div>

        {/* Header */}
        <header className="flex items-center justify-between px-5 py-4 sm:py-5 border-b border-border-light">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-text-primary">
              Filters
            </h2>
            <p className="mt-0.5 text-xs text-text-tertiary">
              Refine your search results
            </p>
          </div>
          <IconButton
            aria-label="Close"
            onClick={() => setFiltersOpen(false)}
          >
            <X className="h-4 w-4" />
          </IconButton>
        </header>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <div className="grid grid-cols-1 gap-4 sm:gap-5">
            <Select
              label="Source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            >
              <option value="">All sources</option>
              {sources.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>

            <div className="grid grid-cols-2 gap-3">
              <Select
                label="Canton"
                value={canton}
                onChange={(e) => setCanton(e.target.value)}
              >
                <option value="">All cantons</option>
                {CANTONS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
              <Select
                label="Language"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
              >
                <option value="">All languages</option>
                {LANGUAGES.map((l) => (
                  <option key={l} value={l}>
                    {l.toUpperCase()}
                  </option>
                ))}
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Select
                label="Seniority"
                value={seniority}
                onChange={(e) => setSeniority(e.target.value)}
              >
                <option value="">All levels</option>
                {SENIORITY.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
              <Select
                label="Contract"
                value={contractType}
                onChange={(e) => setContractType(e.target.value)}
              >
                <option value="">All types</option>
                {CONTRACT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t.replace("_", " ")}
                  </option>
                ))}
              </Select>
            </div>

            <div>
              <span className="block text-sm font-medium text-text-primary mb-1.5">
                Work mode
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setRemoteOnly(false)}
                  className={cn(
                    "h-9 flex-1 rounded-lg border text-sm font-medium transition-colors",
                    !remoteOnly
                      ? "bg-ink text-text-inverse border-ink"
                      : "bg-surface border-border text-text-secondary hover:border-border-strong",
                  )}
                >
                  All
                </button>
                <button
                  type="button"
                  onClick={() => setRemoteOnly(true)}
                  className={cn(
                    "h-9 flex-1 rounded-lg border text-sm font-medium transition-colors",
                    remoteOnly
                      ? "bg-ink text-text-inverse border-ink"
                      : "bg-surface border-border text-text-secondary hover:border-border-strong",
                  )}
                >
                  Remote only
                </button>
              </div>
            </div>

            <div>
              <span className="block text-sm font-medium text-text-primary mb-1.5">
                Salary range (CHF)
              </span>
              <div className="grid grid-cols-2 gap-3">
                <Input
                  type="number"
                  inputMode="numeric"
                  placeholder="Min"
                  value={salaryMin}
                  onChange={(e) => setSalaryMin(e.target.value)}
                />
                <Input
                  type="number"
                  inputMode="numeric"
                  placeholder="Max"
                  value={salaryMax}
                  onChange={(e) => setSalaryMax(e.target.value)}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <footer className="flex items-center gap-3 border-t border-border-light bg-surface px-5 py-4 pb-safe">
          <Button
            variant="ghost"
            leftIcon={<RotateCcw className="h-4 w-4" />}
            onClick={() => resetFilters()}
          >
            Reset
          </Button>
          <Button
            variant="primary"
            fullWidth
            onClick={() => setFiltersOpen(false)}
          >
            Apply filters
          </Button>
        </footer>
      </aside>
    </>
  );
}
