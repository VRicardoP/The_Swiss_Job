import { useEffect } from "react";
import { X, RotateCcw } from "lucide-react";
import { useSearchStore } from "../stores/searchStore";
import { Button, Input, IconButton, cn } from "./ui";

export default function FilterPanel() {
  const {
    source, location, remoteOnly,
    setSource, setLocation, setRemoteOnly,
    filtersOpen, setFiltersOpen, resetFilters,
  } = useSearchStore();

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
            {/* DT-148 A11: era un desplegable alimentado por `/jobs/sources`,
                que devolvia 501 desde el corte de la cosecha — la lista salia
                SIEMPRE vacia y el `.catch(() => {})` lo ocultaba. El filtro
                por fuente SI lo sirve el nucleo, asi que se escribe: una
                fuente, o varias separadas por comas. */}
            <Input
              label="Source"
              placeholder="arbeitnow, ostjob…"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            />

            {/* DT-148 A18-01: aquí había cuatro desplegables —Canton, Language,
                Seniority, Contract— y los cuatro acababan en 501. El content
                canónico no modela esos campos: medido sobre 5.000 vacantes
                activas de producción, la cobertura va del 9,8 % de `canton`
                al 0 % de `seniority`, y en lo cosechado los últimos 14 días
                es peor. Servirlos habría escondido el 91-100 % del corpus.
                `location` los sustituye porque el núcleo SÍ lo filtra, como
                substring sobre un campo presente en el 100 % de las ofertas. */}
            <Input
              label="Location"
              placeholder="Zürich, Genève, Remote…"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
            />

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

            {/* DT-148 A18-01: el rango de salario también daba 501. La canónica
                sólo lleva `salary` como TEXTO LIBRE y no modela los importes
                (salary_min aparece en el 1,1 % de las ofertas activas y en el
                0 % de las últimas dos semanas): filtrar números contra texto
                libre sería inventarse el resultado. Es la cota que el propio
                `/v1` documenta y PERMANECE. */}
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
