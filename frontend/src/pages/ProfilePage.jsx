import { useRef, useState } from "react";
import {
  FileText,
  CheckCircle2,
  Trash2,
  Upload,
  Save,
  Sparkles,
  AlertCircle,
} from "lucide-react";
import {
  useProfile,
  useUpdateProfile,
  useUploadCV,
  useDeleteCV,
} from "../hooks/useProfile";
import TagList from "../components/TagList";
import {
  Button,
  Input,
  Select,
  PageHeader,
  Card,
  cn,
} from "../components/ui";

const REMOTE_OPTIONS = [
  { value: "any", label: "Any" },
  { value: "remote_only", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "Onsite" },
];

const WEIGHTS = [
  { key: "embedding", label: "Skills match" },
  { key: "llm", label: "AI rerank" },
  { key: "salary", label: "Salary fit" },
  { key: "location", label: "Location" },
  { key: "recency", label: "Recency" },
];

function SectionCard({ title, description, children }) {
  return (
    <Card padding="lg" className="space-y-4">
      <header>
        <h2 className="text-base font-semibold tracking-tight text-text-primary">
          {title}
        </h2>
        {description && (
          <p className="mt-0.5 text-sm text-text-secondary">{description}</p>
        )}
      </header>
      {children}
    </Card>
  );
}

export default function ProfilePage() {
  const { data: profile, isLoading, error } = useProfile();
  const updateProfile = useUpdateProfile();
  const uploadCV = useUploadCV();
  const deleteCV = useDeleteCV();
  const fileInputRef = useRef(null);

  const [form, setForm] = useState(null);

  // Hidratamos el formulario una vez que llega el profile.
  // setState durante render (sin useEffect) es el patrón recomendado para
  // derivar estado de props/queries — evita el flash y la regla del lint.
  if (profile && !form) {
    setForm({
      title: profile.title || "",
      skills: profile.skills || [],
      locations: profile.locations || [],
      salary_min: profile.salary_min ?? "",
      salary_max: profile.salary_max ?? "",
      remote_pref: profile.remote_pref || "any",
      score_weights: profile.score_weights || {
        embedding: 0.3,
        llm: 0.1,
        salary: 0.2,
        location: 0.25,
        recency: 0.15,
      },
    });
  }

  if (isLoading || !form) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-ink" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <div className="rounded-xl border border-error-border bg-error-light p-4 text-sm text-error">
          Error loading profile: {error.message}
        </div>
      </div>
    );
  }

  function setField(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function setWeight(key, value) {
    const num = parseFloat(value);
    if (Number.isNaN(num)) return;
    setForm((prev) => ({
      ...prev,
      score_weights: { ...prev.score_weights, [key]: num },
    }));
  }

  function handleSave() {
    updateProfile.mutate({
      title: form.title || null,
      skills: form.skills,
      locations: form.locations,
      salary_min: form.salary_min ? Number(form.salary_min) : null,
      salary_max: form.salary_max ? Number(form.salary_max) : null,
      remote_pref: form.remote_pref,
      score_weights: form.score_weights,
    });
  }

  function handleUploadCV(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadCV.mutate(file);
  }

  const weightsSum = Object.values(form.score_weights).reduce(
    (a, b) => a + b,
    0,
  );
  const weightsValid = Math.abs(weightsSum - 1.0) <= 0.02;

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        eyebrow="Account"
        title="Profile"
        description="Manage how SwissJob understands what you're looking for and how matches are scored."
      />

      <div className="mt-6 space-y-5">
        {/* Identity */}
        <SectionCard
          title="Role & identity"
          description="The headline role you're targeting. Used as context for the AI ranking."
        >
          <Input
            label="Job title / role"
            value={form.title}
            onChange={(e) => setField("title", e.target.value)}
            placeholder="e.g. Senior Backend Engineer, AI Evaluator"
          />
        </SectionCard>

        {/* CV */}
        <SectionCard
          title="CV / Resume"
          description="Upload your CV — we extract skills and create a semantic embedding for matching."
        >
          {profile.cv_text ? (
            <div className="flex items-start gap-3 rounded-lg border border-success-border bg-success-light p-3">
              <CheckCircle2 className="h-5 w-5 shrink-0 text-success" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-text-primary">
                  CV uploaded
                </p>
                <p className="text-xs text-text-secondary">
                  {profile.cv_text.length.toLocaleString()} characters
                  {profile.has_cv_embedding && " · Embedding generated"}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                leftIcon={<Trash2 className="h-3.5 w-3.5" />}
                loading={deleteCV.isPending}
                onClick={() => deleteCV.mutate()}
                className="text-error hover:bg-error-light"
              >
                Delete
              </Button>
            </div>
          ) : (
            <div className="flex items-start gap-3 rounded-lg border border-dashed border-border bg-surface-secondary p-3">
              <FileText className="h-5 w-5 shrink-0 text-text-tertiary" aria-hidden="true" />
              <p className="text-sm text-text-secondary">No CV uploaded yet.</p>
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx"
            onChange={handleUploadCV}
            className="hidden"
          />
          <Button
            variant="primary"
            leftIcon={<Upload className="h-4 w-4" />}
            loading={uploadCV.isPending}
            onClick={() => fileInputRef.current?.click()}
          >
            {profile.cv_text ? "Replace CV (PDF / DOCX)" : "Upload CV (PDF / DOCX)"}
          </Button>

          {uploadCV.isSuccess && (
            <p className="flex items-center gap-1.5 text-sm text-success">
              <CheckCircle2 className="h-4 w-4" />
              CV uploaded — {uploadCV.data.skills_extracted.length} skills extracted.
            </p>
          )}
          {uploadCV.isError && (
            <p className="flex items-center gap-1.5 text-sm text-error">
              <AlertCircle className="h-4 w-4" />
              {uploadCV.error.message}
            </p>
          )}
        </SectionCard>

        {/* Skills */}
        <SectionCard
          title="Skills, languages & certifications"
          description="Add anything that should weigh in your match score."
        >
          <TagList
            items={form.skills}
            onChange={(next) => setField("skills", next)}
            placeholder="Add skill, language, tool, certification..."
            variant="brand"
            emptyText="No skills yet — they boost your matching accuracy."
            inputAriaLabel="Add skill"
          />
        </SectionCard>

        {/* Locations & remote */}
        <SectionCard
          title="Preferences"
          description="Where you want to work and your salary expectations."
        >
          <div className="space-y-2">
            <span className="block text-sm font-medium text-text-primary">
              Preferred locations
            </span>
            <TagList
              items={form.locations}
              onChange={(next) => setField("locations", next)}
              placeholder="e.g. Zurich, Lausanne"
              emptyText="No preferred locations yet."
            />
          </div>

          <Select
            label="Remote preference"
            value={form.remote_pref}
            onChange={(e) => setField("remote_pref", e.target.value)}
          >
            {REMOTE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>

          <div>
            <span className="mb-1.5 block text-sm font-medium text-text-primary">
              Salary range (CHF / year)
            </span>
            <div className="grid grid-cols-2 gap-3">
              <Input
                type="number"
                inputMode="numeric"
                placeholder="Min"
                value={form.salary_min}
                onChange={(e) => setField("salary_min", e.target.value)}
              />
              <Input
                type="number"
                inputMode="numeric"
                placeholder="Max"
                value={form.salary_max}
                onChange={(e) => setField("salary_max", e.target.value)}
              />
            </div>
          </div>
        </SectionCard>

        {/* Score weights */}
        <SectionCard
          title="Match scoring weights"
          description="Fine-tune how the AI scores matches. Must sum to 1.00."
        >
          <div className="space-y-3">
            {WEIGHTS.map(({ key, label }) => {
              const v = form.score_weights[key] ?? 0;
              return (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-28 text-sm text-text-primary">{label}</span>
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={v}
                    onChange={(e) => setWeight(key, e.target.value)}
                    className="flex-1 accent-ink"
                    aria-label={`${label} weight`}
                  />
                  <span className="w-12 text-right text-sm font-medium tabular-nums text-text-primary">
                    {v.toFixed(2)}
                  </span>
                </div>
              );
            })}
          </div>

          <div
            className={cn(
              "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium",
              weightsValid
                ? "border-success-border bg-success-light text-success"
                : "border-error-border bg-error-light text-error",
            )}
          >
            {weightsValid ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <AlertCircle className="h-4 w-4" />
            )}
            Total: {weightsSum.toFixed(2)}
            {!weightsValid && " — must be ≈ 1.00"}
          </div>
        </SectionCard>
      </div>

      {/* Sticky save bar */}
      <div className="sticky bottom-20 mt-6 lg:bottom-4">
        <div
          className={cn(
            "flex items-center justify-between gap-3 rounded-xl border border-border bg-surface/95 backdrop-blur-lg p-3 shadow-card-hover",
          )}
        >
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <Sparkles className="h-4 w-4 text-text-tertiary" aria-hidden="true" />
            <span>
              Changes apply to new analyses.{" "}
              {updateProfile.isSuccess && (
                <span className="text-success">Saved.</span>
              )}
              {updateProfile.isError && (
                <span className="text-error">
                  {updateProfile.error.message}
                </span>
              )}
            </span>
          </div>
          <Button
            variant="primary"
            leftIcon={<Save className="h-4 w-4" />}
            loading={updateProfile.isPending}
            disabled={!weightsValid}
            onClick={handleSave}
          >
            Save profile
          </Button>
        </div>
      </div>
    </div>
  );
}
