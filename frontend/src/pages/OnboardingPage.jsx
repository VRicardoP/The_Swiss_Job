import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Upload,
  FileText,
  Sparkles,
  ChevronRight,
  ChevronLeft,
  Check,
  CheckCircle2,
} from "lucide-react";
import { useProfile, useUpdateProfile, useUploadCV } from "../hooks/useProfile";
import TagList from "../components/TagList";
import { Button, Input, Select, cn } from "../components/ui";

const STEPS = [
  { id: "cv",     label: "Upload CV",      hint: "Extract skills automatically" },
  { id: "skills", label: "Confirm skills", hint: "Tune what we know about you" },
  { id: "prefs",  label: "Preferences",    hint: "Location, remote, salary" },
  { id: "ready",  label: "Ready",          hint: "Find your first matches" },
];

const REMOTE_OPTIONS = [
  { value: "any", label: "Any" },
  { value: "remote_only", label: "Remote only" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

function StepDots({ current }) {
  return (
    <div className="flex items-center justify-center gap-2">
      {STEPS.map((s, i) => (
        <div key={s.id} className="flex items-center gap-2">
          <span
            className={cn(
              "flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-colors",
              i < current && "bg-ink text-text-inverse",
              i === current && "bg-ink text-text-inverse ring-4 ring-ink/10",
              i > current && "bg-surface-tertiary text-text-tertiary",
            )}
          >
            {i < current ? <Check className="h-3.5 w-3.5" /> : i + 1}
          </span>
          {i < STEPS.length - 1 && (
            <div
              className={cn(
                "h-px w-6 transition-colors sm:w-10",
                i < current ? "bg-ink" : "bg-border",
              )}
            />
          )}
        </div>
      ))}
    </div>
  );
}

function StepShell({ title, description, children, footer }) {
  return (
    <div className="rounded-2xl border border-border bg-surface shadow-card animate-fade-in-up">
      <div className="px-6 py-6 sm:px-8 sm:py-8">
        <h2 className="text-xl font-semibold tracking-tight text-text-primary">
          {title}
        </h2>
        {description && (
          <p className="mt-1.5 text-sm text-text-secondary">{description}</p>
        )}
        <div className="mt-5">{children}</div>
      </div>
      {footer && (
        <footer className="flex items-center justify-between gap-3 border-t border-border-light px-6 py-4 sm:px-8">
          {footer}
        </footer>
      )}
    </div>
  );
}

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { data: profile, isLoading } = useProfile();
  const uploadCV = useUploadCV();
  const updateProfile = useUpdateProfile();
  const fileInputRef = useRef(null);

  const [step, setStep] = useState(0);
  const [skills, setSkills] = useState([]);
  const [locations, setLocations] = useState([]);
  const [salaryMin, setSalaryMin] = useState("");
  const [salaryMax, setSalaryMax] = useState("");
  const [remotePref, setRemotePref] = useState("any");
  const [cvUploaded, setCvUploaded] = useState(false);
  const [initialized, setInitialized] = useState(false);

  if (profile && !initialized) {
    setInitialized(true);
    if (profile.skills?.length) setSkills(profile.skills);
    if (profile.locations?.length) setLocations(profile.locations);
    if (profile.salary_min) setSalaryMin(profile.salary_min);
    if (profile.salary_max) setSalaryMax(profile.salary_max);
    if (profile.remote_pref) setRemotePref(profile.remote_pref);
    if (profile.cv_text) setCvUploaded(true);
  }

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-ink" />
      </div>
    );
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const result = await uploadCV.mutateAsync(file);
    if (result.skills_extracted?.length > 0) {
      setSkills((prev) => [...new Set([...prev, ...result.skills_extracted])]);
    }
    setCvUploaded(true);
  }

  async function handleFinish() {
    await updateProfile.mutateAsync({
      skills,
      locations,
      salary_min: salaryMin ? Number(salaryMin) : null,
      salary_max: salaryMax ? Number(salaryMax) : null,
      remote_pref: remotePref,
    });
    navigate("/match");
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 sm:py-12">
      {/* Hero */}
      <div className="mb-8 text-center">
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-ink text-text-inverse">
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="2" y="2" width="20" height="20" rx="3" className="fill-swiss-red" />
            <path d="M7 12h10M12 7v10" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
        </span>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight text-text-primary">
          Welcome to SwissJob
        </h1>
        <p className="mt-2 text-sm text-text-secondary">
          {STEPS[step].hint}
        </p>
      </div>

      <div className="mb-6">
        <StepDots current={step} />
      </div>

      {/* Step 0 — Upload CV */}
      {step === 0 && (
        <StepShell
          title="Upload your CV"
          description="We'll extract skills, experience and create a semantic embedding to power AI matching."
          footer={
            <>
              <span className="text-xs text-text-tertiary">Optional — you can skip</span>
              <Button
                variant="primary"
                rightIcon={<ChevronRight className="h-4 w-4" />}
                onClick={() => setStep(1)}
              >
                {cvUploaded ? "Continue" : "Skip for now"}
              </Button>
            </>
          }
        >
          <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed border-border bg-surface-secondary px-6 py-10 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-ink-50 text-ink-700">
              <FileText className="h-6 w-6" aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-medium text-text-primary">
                Drop your CV here or click to choose
              </p>
              <p className="mt-0.5 text-xs text-text-tertiary">
                PDF or DOCX · max ~5 MB
              </p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx"
              onChange={handleUpload}
              className="hidden"
            />
            <Button
              variant="primary"
              leftIcon={<Upload className="h-4 w-4" />}
              loading={uploadCV.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              Choose file
            </Button>
            {cvUploaded && (
              <p className="flex items-center gap-1.5 text-sm text-success">
                <CheckCircle2 className="h-4 w-4" />
                CV uploaded successfully
              </p>
            )}
            {uploadCV.isError && (
              <p className="text-sm text-error">{uploadCV.error.message}</p>
            )}
          </div>
        </StepShell>
      )}

      {/* Step 1 — Confirm skills */}
      {step === 1 && (
        <StepShell
          title="Confirm your skills"
          description="Add or remove skills, languages, tools and certifications."
          footer={
            <>
              <Button variant="ghost" leftIcon={<ChevronLeft className="h-4 w-4" />} onClick={() => setStep(0)}>
                Back
              </Button>
              <Button variant="primary" rightIcon={<ChevronRight className="h-4 w-4" />} onClick={() => setStep(2)}>
                Continue
              </Button>
            </>
          }
        >
          <TagList
            items={skills}
            onChange={setSkills}
            placeholder="Add skill, language, tool, certification..."
            variant="brand"
            emptyText="No skills yet. Add a few to improve match accuracy."
            inputAriaLabel="Add skill"
          />
        </StepShell>
      )}

      {/* Step 2 — Preferences */}
      {step === 2 && (
        <StepShell
          title="Preferences"
          description="Tell us where you want to work and your salary expectations."
          footer={
            <>
              <Button variant="ghost" leftIcon={<ChevronLeft className="h-4 w-4" />} onClick={() => setStep(1)}>
                Back
              </Button>
              <Button variant="primary" rightIcon={<ChevronRight className="h-4 w-4" />} onClick={() => setStep(3)}>
                Continue
              </Button>
            </>
          }
        >
          <div className="space-y-5">
            <div>
              <span className="mb-1.5 block text-sm font-medium text-text-primary">
                Preferred locations
              </span>
              <TagList
                items={locations}
                onChange={setLocations}
                placeholder="e.g. Zurich, Lausanne"
                emptyText="Add a few cities you'd accept."
              />
            </div>

            <Select
              label="Remote preference"
              value={remotePref}
              onChange={(e) => setRemotePref(e.target.value)}
            >
              {REMOTE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
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
        </StepShell>
      )}

      {/* Step 3 — Ready */}
      {step === 3 && (
        <StepShell
          title="You're all set"
          description="We'll use your profile to fetch matches as soon as you start."
          footer={
            <>
              <Button variant="ghost" leftIcon={<ChevronLeft className="h-4 w-4" />} onClick={() => setStep(2)}>
                Back
              </Button>
              <Button
                variant="primary"
                leftIcon={<Sparkles className="h-4 w-4" />}
                loading={updateProfile.isPending}
                onClick={handleFinish}
              >
                Find matches
              </Button>
            </>
          }
        >
          <div className="space-y-3 rounded-xl bg-surface-secondary p-4">
            <div className="flex items-center justify-between text-sm">
              <span className="text-text-secondary">CV uploaded</span>
              <span className="font-medium text-text-primary">
                {cvUploaded ? "Yes" : "Skipped"}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-text-secondary">Skills</span>
              <span className="font-medium text-text-primary tabular-nums">
                {skills.length}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-text-secondary">Locations</span>
              <span className="font-medium text-text-primary tabular-nums">
                {locations.length}
              </span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-text-secondary">Remote</span>
              <span className="font-medium text-text-primary">
                {REMOTE_OPTIONS.find((o) => o.value === remotePref)?.label}
              </span>
            </div>
            {salaryMin && salaryMax && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-text-secondary">Salary</span>
                <span className="font-medium tabular-nums text-text-primary">
                  CHF {Number(salaryMin).toLocaleString()} –{" "}
                  {Number(salaryMax).toLocaleString()}
                </span>
              </div>
            )}
          </div>
        </StepShell>
      )}
    </div>
  );
}
