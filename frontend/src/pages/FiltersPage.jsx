import { useState } from 'react'
import { useAnalyzeSuggestions, useSuggestions, useReviewSuggestion, useFilters, useDeleteFilter, useCreateFilter } from '../hooks/useAnalytics'

const CONFIDENCE_COLOR = (c) => {
  if (c >= 0.75) return 'text-error bg-swiss-red-light'
  if (c >= 0.5) return 'text-warning bg-amber-50'
  return 'text-text-secondary bg-surface-secondary'
}

function SuggestionCard({ suggestion, onApprove, onReject, loading }) {
  const [expanded, setExpanded] = useState(false)
  const pct = Math.round(suggestion.confidence * 100)

  return (
    <div className="rounded-xl border border-border bg-surface shadow-card p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1 flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${CONFIDENCE_COLOR(suggestion.confidence)}`}>
              {pct}% confianza
            </span>
            <span className="text-xs text-text-tertiary bg-surface-secondary px-2 py-0.5 rounded-full">
              {suggestion.suggestion_type === 'title_pattern' ? 'Patrón de título' : 'Tag / categoría'}
            </span>
            <span className="text-xs text-text-tertiary">
              {suggestion.affected_count} job{suggestion.affected_count !== 1 ? 's' : ''} rechazados
            </span>
          </div>
          <p className="text-sm font-semibold text-text-primary font-mono mt-1">
            &ldquo;{suggestion.pattern}&rdquo;
          </p>
          <p className="text-sm text-text-secondary">{suggestion.description}</p>
        </div>
      </div>

      {suggestion.sample_jobs?.length > 0 && (
        <div>
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-xs text-swiss-red hover:underline"
          >
            {expanded ? 'Ocultar ejemplos' : `Ver ${suggestion.sample_jobs.length} ejemplo${suggestion.sample_jobs.length !== 1 ? 's' : ''}`}
          </button>
          {expanded && (
            <ul className="mt-2 flex flex-col gap-1">
              {suggestion.sample_jobs.map((j, i) => (
                <li key={i} className="text-xs text-text-secondary bg-surface-secondary rounded-lg px-3 py-1.5">
                  <span className="font-medium text-text-primary">{j.title}</span>
                  {j.company && <span className="text-text-tertiary"> — {j.company}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex gap-2 pt-1">
        <button
          onClick={() => onApprove(suggestion.id)}
          disabled={loading}
          className="flex-1 rounded-lg bg-swiss-red hover:bg-swiss-red-hover text-white text-sm font-semibold py-2 transition-colors disabled:opacity-50"
        >
          Activar filtro
        </button>
        <button
          onClick={() => onReject(suggestion.id)}
          disabled={loading}
          className="flex-1 rounded-lg border border-border hover:bg-surface-secondary text-text-secondary text-sm py-2 transition-colors disabled:opacity-50"
        >
          Descartar
        </button>
      </div>
    </div>
  )
}

function FilterBadge({ filter, onDelete }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface shadow-xs px-4 py-3">
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <span className="text-xs bg-surface-secondary text-text-secondary px-2 py-0.5 rounded-full shrink-0">
          {filter.filter_type === 'title_contains' ? 'título' : 'tag'}
        </span>
        <span className="text-sm font-mono font-medium text-text-primary truncate">
          &ldquo;{filter.pattern}&rdquo;
        </span>
        {filter.hit_count > 0 && (
          <span className="text-xs text-text-tertiary shrink-0">
            {filter.hit_count} filtrado{filter.hit_count !== 1 ? 's' : ''}
          </span>
        )}
        <span className="text-xs text-text-tertiary shrink-0">
          {filter.source === 'manual' ? '· manual' : '· auto'}
        </span>
      </div>
      <button
        onClick={() => onDelete(filter.id)}
        className="text-text-tertiary hover:text-error transition-colors"
        title="Eliminar filtro"
      >
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  )
}

function AddFilterForm({ onAdd }) {
  const [type, setType] = useState('title_contains')
  const [pattern, setPattern] = useState('')
  const [desc, setDesc] = useState('')
  const [open, setOpen] = useState(false)

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!pattern.trim()) return
    onAdd({ filter_type: type, pattern: pattern.trim().toLowerCase(), description: desc.trim() || null })
    setPattern('')
    setDesc('')
    setOpen(false)
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full rounded-xl border border-dashed border-border hover:border-swiss-red text-text-tertiary hover:text-swiss-red text-sm py-3 transition-colors"
      >
        + Añadir filtro manual
      </button>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-xl border border-border bg-surface p-4 flex flex-col gap-3">
      <p className="text-sm font-semibold text-text-primary">Nuevo filtro manual</p>
      <div className="flex gap-2">
        <select
          value={type}
          onChange={e => setType(e.target.value)}
          className="rounded-lg border border-border bg-surface-secondary text-sm px-3 py-2"
        >
          <option value="title_contains">Título contiene</option>
          <option value="tag_contains">Tag contiene</option>
        </select>
        <input
          type="text"
          value={pattern}
          onChange={e => setPattern(e.target.value)}
          placeholder='ej: "developer", "java"'
          className="flex-1 rounded-lg border border-border bg-surface-secondary text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-swiss-red"
          required
        />
      </div>
      <input
        type="text"
        value={desc}
        onChange={e => setDesc(e.target.value)}
        placeholder="Descripción opcional"
        className="rounded-lg border border-border bg-surface-secondary text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-swiss-red"
      />
      <div className="flex gap-2">
        <button type="submit" className="flex-1 rounded-lg bg-swiss-red hover:bg-swiss-red-hover text-white text-sm font-semibold py-2 transition-colors">
          Crear
        </button>
        <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-border text-text-secondary text-sm px-4 py-2 hover:bg-surface-secondary transition-colors">
          Cancelar
        </button>
      </div>
    </form>
  )
}

export default function FiltersPage() {
  const [tab, setTab] = useState('suggestions')

  const { mutate: analyze, isPending: analyzing } = useAnalyzeSuggestions()
  const { data: suggestionsData, isLoading: loadingSuggestions } = useSuggestions('pending')
  const { mutate: review, isPending: reviewing } = useReviewSuggestion()
  const { data: filtersData, isLoading: loadingFilters } = useFilters()
  const { mutate: deleteFilter } = useDeleteFilter()
  const { mutate: createFilter } = useCreateFilter()

  const suggestions = suggestionsData?.data ?? []
  const filters = filtersData?.data ?? []

  const handleApprove = (id) => review({ id, action: 'approve' })
  const handleReject = (id) => review({ id, action: 'reject' })

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Filtros de exclusión</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Analiza tus jobs rechazados para detectar patrones y crea filtros que mejoren la calidad de los matches.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-xl bg-surface-secondary p-1 mb-6">
        {[
          { key: 'suggestions', label: `Sugerencias${suggestions.length ? ` (${suggestions.length})` : ''}` },
          { key: 'filters', label: `Filtros activos${filters.length ? ` (${filters.length})` : ''}` },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex-1 rounded-lg py-2 text-sm font-semibold transition-all ${
              tab === key
                ? 'bg-surface shadow-xs text-text-primary'
                : 'text-text-tertiary hover:text-text-secondary'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Panel sugerencias */}
      {tab === 'suggestions' && (
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-text-secondary">
              El análisis examina los títulos y tags de tus jobs rechazados para detectar patrones con alta tasa de rechazo.
            </p>
            <button
              onClick={() => analyze({ min_rejected: 2 })}
              disabled={analyzing}
              className="shrink-0 rounded-lg bg-swiss-red hover:bg-swiss-red-hover text-white text-sm font-semibold px-4 py-2 transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {analyzing && (
                <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                </svg>
              )}
              {analyzing ? 'Analizando…' : 'Analizar rechazados'}
            </button>
          </div>

          {loadingSuggestions ? (
            <div className="flex justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
            </div>
          ) : suggestions.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border py-12 text-center">
              <p className="text-text-secondary text-sm">No hay sugerencias pendientes.</p>
              <p className="text-text-tertiary text-xs mt-1">
                Haz clic en &ldquo;Analizar rechazados&rdquo; para generar sugerencias a partir de tus jobs rechazados.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {suggestions.map((s) => (
                <SuggestionCard
                  key={s.id}
                  suggestion={s}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  loading={reviewing}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Panel filtros activos */}
      {tab === 'filters' && (
        <div className="flex flex-col gap-3">
          {loadingFilters ? (
            <div className="flex justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
            </div>
          ) : (
            <>
              {filters.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border py-8 text-center mb-2">
                  <p className="text-text-secondary text-sm">No tienes filtros activos.</p>
                  <p className="text-text-tertiary text-xs mt-1">
                    Aprueba sugerencias o añade filtros manuales para excluir categorías de tu matching.
                  </p>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {filters.map((f) => (
                    <FilterBadge
                      key={f.id}
                      filter={f}
                      onDelete={(id) => deleteFilter(id)}
                    />
                  ))}
                </div>
              )}
              <AddFilterForm onAdd={(data) => createFilter(data)} />
              <p className="text-xs text-text-tertiary text-center mt-1">
                Los filtros activos se aplican en el próximo análisis de matches.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
