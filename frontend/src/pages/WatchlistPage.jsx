import { useState, useMemo } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import {
  GraduationCap,
  Flame,
  Mail,
  ExternalLink,
  FileText,
  Calendar,
  ChevronRight,
  Loader2,
} from "lucide-react";
import { matchApi, watchlistApi } from "../config/api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
  cn,
} from "../components/ui";

// State machine de candidatura — orden y etiquetas.
const STATES = [
  { id: "detected",         label: "Detectado",     tone: "bg-ink-200" },
  { id: "reviewed",         label: "Revisado",      tone: "bg-info" },
  { id: "drafted",          label: "Borrador",      tone: "bg-warning" },
  { id: "sent",             label: "Enviado",       tone: "bg-info" },
  { id: "awaiting",         label: "Esperando",     tone: "bg-warning" },
  { id: "followup_due",     label: "Follow-up",     tone: "bg-warning" },
  { id: "interview",        label: "Entrevista",    tone: "bg-swiss-red" },
  { id: "closed_positive",  label: "Cerrado +",     tone: "bg-success" },
  { id: "closed_negative",  label: "Cerrado −",     tone: "bg-error" },
];

const NEXT = {
  detected:        "reviewed",
  reviewed:        "drafted",
  drafted:         "sent",
  sent:            "awaiting",
  awaiting:        "followup_due",
  followup_due:    "interview",
  interview:       "closed_positive",
};


export default function WatchlistPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("all");
  const [openDraft, setOpenDraft] = useState(null); // job_hash with open drawer

  const { data: results, isLoading } = useQuery({
    queryKey: ["match-results", 500],
    queryFn: () => matchApi.getResults({ limit: 500, translate: false }),
  });

  const watchlist = useMemo(() => {
    const all = (results?.data ?? []).filter((m) => m.school_id);
    return all.sort(
      (a, b) =>
        b.score_final + (b.urgency_score || 0) -
        (a.score_final + (a.urgency_score || 0)),
    );
  }, [results]);

  const byStatus = useMemo(() => {
    const m = new Map();
    for (const r of watchlist) {
      const s = r.application_status || "detected";
      if (!m.has(s)) m.set(s, []);
      m.get(s).push(r);
    }
    return m;
  }, [watchlist]);

  const visible =
    statusFilter === "all"
      ? watchlist
      : byStatus.get(statusFilter) ?? [];

  const setStatus = useMutation({
    mutationFn: ({ jobHash, status }) => watchlistApi.setStatus(jobHash, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["match-results", 500] }),
  });

  const generateDraft = useMutation({
    mutationFn: ({ jobHash }) => watchlistApi.generateDraft(jobHash),
    onSuccess: (data, vars) => {
      qc.invalidateQueries({ queryKey: ["match-results", 500] });
      setOpenDraft(vars.jobHash);
    },
  });

  async function downloadIcs(jobHash) {
    const token = localStorage.getItem("swissjob_token");
    const res = await fetch(`/api/v1/watchlist/match/${jobHash}/calendar.ics`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `swissjob-${jobHash}.ics`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        eyebrow="Watchlist colegios"
        title="Vigilancia de candidaturas"
        description="Lista cerrada de colegios suizos. Cada vacante incluye contexto de candidatura (a quién escribir, qué plantilla usar)."
      />

      {/* Filtro por estado */}
      <div className="mb-4 flex flex-wrap gap-2">
        <FilterPill
          active={statusFilter === "all"}
          label="Todos"
          count={watchlist.length}
          onClick={() => setStatusFilter("all")}
        />
        {STATES.map((s) => {
          const count = (byStatus.get(s.id) ?? []).length;
          if (count === 0) return null;
          return (
            <FilterPill
              key={s.id}
              active={statusFilter === s.id}
              label={s.label}
              count={count}
              tone={s.tone}
              onClick={() => setStatusFilter(s.id)}
            />
          );
        })}
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<GraduationCap className="h-10 w-10" />}
          title="Aún no hay vacantes en la watchlist"
          description="Cuando los scrapers de los colegios suizos detecten nuevas vacantes aparecerán aquí."
        />
      ) : (
        <div className="space-y-3">
          {visible.map((m) => (
            <WatchlistRow
              key={m.id}
              match={m}
              onAdvance={() => {
                const next = NEXT[m.application_status ?? "detected"];
                if (next) setStatus.mutate({ jobHash: m.job_hash, status: next });
              }}
              onDraft={() => generateDraft.mutate({ jobHash: m.job_hash })}
              onIcs={() => downloadIcs(m.job_hash)}
              draftLoading={
                generateDraft.isPending && generateDraft.variables?.jobHash === m.job_hash
              }
              draftOpen={openDraft === m.job_hash}
              onCloseDraft={() => setOpenDraft(null)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FilterPill({ active, label, count, tone, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "border-ink bg-ink text-text-inverse"
          : "border-border bg-surface text-text-secondary hover:border-border-strong",
      )}
    >
      {tone && (
        <span className={cn("h-1.5 w-1.5 rounded-full", tone)} />
      )}
      {label}
      <span className={cn(
        "rounded-full px-1.5 text-[10px] tabular-nums",
        active ? "bg-text-inverse/15" : "bg-surface-tertiary",
      )}>{count}</span>
    </button>
  );
}

function WatchlistRow({ match, onAdvance, onDraft, onIcs, draftLoading, draftOpen, onCloseDraft }) {
  const status = match.application_status || "detected";
  const stateMeta = STATES.find((s) => s.id === status) ?? STATES[0];
  const next = NEXT[status];

  return (
    <Card className="p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="success" size="xs" leftIcon={<GraduationCap className="h-3 w-3" />}>
              {match.school_id}
            </Badge>
            {match.school_policy && (
              <Badge variant="neutral" size="xs">{match.school_policy}</Badge>
            )}
            {match.urgency_score >= 30 && (
              <Badge variant="warning" size="xs" leftIcon={<Flame className="h-3 w-3" />}>
                urg {Math.round(match.urgency_score)}
              </Badge>
            )}
            <Badge variant="info" size="xs">
              <span className={cn("mr-1 inline-block h-1.5 w-1.5 rounded-full", stateMeta.tone)} />
              {stateMeta.label}
            </Badge>
          </div>

          <a
            href={match.job_url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 block text-[15px] font-semibold text-text-primary hover:text-ink"
          >
            {match.job_title_en || match.job_title}
          </a>
          <p className="mt-0.5 text-sm text-text-secondary">
            {match.job_company}
            {match.job_location && ` · ${match.job_location}`}
          </p>
        </div>

        <div className="text-right">
          <p className="text-2xl font-semibold tabular-nums text-text-primary">
            {Math.round(match.score_final)}
          </p>
          <p className="text-[11px] uppercase tracking-wider text-text-tertiary">score</p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border-light pt-3">
        {next && (
          <Button size="sm" variant="ghost" onClick={onAdvance}>
            <ChevronRight className="h-3.5 w-3.5" />
            {STATES.find((s) => s.id === next)?.label}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          onClick={onDraft}
          disabled={draftLoading}
        >
          {draftLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}
          {match.has_draft ? "Re-generar carta" : "Generar borrador"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onIcs}>
          <Calendar className="h-3.5 w-3.5" />
          .ics
        </Button>
        <a
          href={match.job_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-surface px-2 text-xs font-medium text-text-primary hover:border-border-strong"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Oferta
        </a>
      </div>

      {draftOpen && (
        <DraftDrawer jobHash={match.job_hash} onClose={onCloseDraft} />
      )}
    </Card>
  );
}

function DraftDrawer({ jobHash, onClose }) {
  const { data: draft, isLoading } = useQuery({
    queryKey: ["draft", jobHash],
    queryFn: () => watchlistApi.getDraft(jobHash),
  });

  return (
    <div className="mt-3 rounded-lg border border-info-border bg-info-light/40 p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold text-info">
          <Mail className="mr-1 inline h-3.5 w-3.5" />
          Borrador de carta
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-text-tertiary hover:text-text-primary"
        >
          cerrar
        </button>
      </div>
      <pre className="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-words rounded bg-surface p-3 text-[12px] leading-relaxed text-text-primary">
        {isLoading ? "Cargando..." : (draft || "")}
      </pre>
    </div>
  );
}
