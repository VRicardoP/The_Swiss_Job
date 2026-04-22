import { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useProfile, useUpdateProfile, useUploadCV } from "../hooks/useProfile";

const STEPS = ["Upload CV", "Confirm Skills", "Preferences", "Ready"];

const REMOTE_OPTIONS = [
  { value: "any", label: "Any" },
  { value: "remote_only", label: "Remote Only" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

function StepIndicator({ current }) {
  return (
    <div className="mb-6 flex items-center justify-center gap-2">
      {STEPS.map((label, i) => (
        <div key={label} className="flex items-center gap-2">
          <div
            className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-medium ${
              i < current
                ? "bg-success text-white"
                : i === current
                  ? "bg-swiss-red text-white"
                  : "bg-surface-tertiary text-text-tertiary"
            }`}
          >
            {i < current ? "\u2713" : i + 1}
          </div>
          <span
            className={`hidden text-xs sm:inline ${
              i === current ? "font-medium text-text-primary" : "text-text-tertiary"
            }`}
          >
            {label}
          </span>
          {i < STEPS.length - 1 && (
            <div className="h-0.5 w-8 bg-border" />
          )}
        </div>
      ))}
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
  const [newSkill, setNewSkill] = useState("");
  const [locations, setLocations] = useState([]);
  const [newLoc, setNewLoc] = useState("");
  const [salaryMin, setSalaryMin] = useState("");
  const [salaryMax, setSalaryMax] = useState("");
  const [remotePref, setRemotePref] = useState("any");
  const [cvUploaded, setCvUploaded] = useState(false);

  // Initialize from profile if exists
  if (profile && !cvUploaded && skills.length === 0 && profile.skills?.length > 0) {
    setSkills(profile.skills);
    if (profile.locations?.length) setLocations(profile.locations);
    if (profile.salary_min) setSalaryMin(profile.salary_min);
    if (profile.salary_max) setSalaryMax(profile.salary_max);
    if (profile.remote_pref) setRemotePref(profile.remote_pref);
    if (profile.cv_text) setCvUploaded(true);
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const result = await uploadCV.mutateAsync(file);
    if (result.skills_extracted?.length > 0) {
      setSkills((prev) => [
        ...new Set([...prev, ...result.skills_extracted]),
      ]);
    }
    setCvUploaded(true);
  }

  function addSkill(e) {
    e.preventDefault();
    const s = newSkill.trim();
    if (s && !skills.includes(s)) setSkills((prev) => [...prev, s]);
    setNewSkill("");
  }

  function addLocation(e) {
    e.preventDefault();
    const loc = newLoc.trim();
    if (loc && !locations.includes(loc)) setLocations((prev) => [...prev, loc]);
    setNewLoc("");
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
    <div className="mx-auto max-w-lg p-4 pb-20">
      {/* Swiss logo + Welcome */}
      <div className="mb-2 flex flex-col items-center">
        <svg className="h-10 w-10 mb-3" viewBox="0 0 32 32" fill="none">
          <rect width="32" height="32" rx="6" className="fill-swiss-red" />
          <path d="M10 16h12M16 10v12" stroke="white" strokeWidth="3.5" strokeLinecap="round" />
        </svg>
        <h1 className="text-3xl font-bold text-text-primary tracking-tight text-center">
          Welcome to SwissJob
        </h1>
      </div>
      <p className="mb-6 text-center text-sm text-text-secondary">
        Let's set up your profile to find the best matches
      </p>

      <StepIndicator current={step} />

      {/* Step 0: Upload CV */}
      {step === 0 && (
        <div className="bg-surface shadow-card rounded-2xl p-8 text-center">
          <h2 className="mb-2 text-lg font-medium text-text-primary">Upload your CV</h2>
          <p className="mb-4 text-sm text-text-secondary">
            We'll extract your skills and experience to find matching jobs.
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx"
            onChange={handleUpload}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadCV.isPending}
            className="rounded-xl bg-swiss-red px-6 py-2 text-white shadow-xs hover:bg-swiss-red-hover disabled:opacity-50"
          >
            {uploadCV.isPending ? "Uploading..." : "Choose File (PDF/DOCX)"}
          </button>
          {cvUploaded && (
            <p className="mt-3 text-sm text-success">CV uploaded successfully!</p>
          )}
          {uploadCV.isError && (
            <p className="mt-3 text-sm text-error">{uploadCV.error.message}</p>
          )}
          <div className="mt-4">
            <button
              onClick={() => setStep(1)}
              className="text-sm text-text-tertiary hover:text-swiss-red"
            >
              {cvUploaded ? "Next" : "Skip for now"}
            </button>
          </div>
        </div>
      )}

      {/* Step 1: Confirm Skills */}
      {step === 1 && (
        <div className="bg-surface shadow-card rounded-2xl p-8">
          <h2 className="mb-2 text-lg font-medium text-text-primary">Confirm your skills & certifications</h2>
          <p className="mb-4 text-sm text-text-secondary">
            Add or remove skills, languages, tools, and certifications to improve your match quality.
          </p>
          <div className="mb-3 flex flex-wrap gap-2">
            {skills.map((s) => (
              <span
                key={s}
                className="inline-flex items-center gap-1 rounded-full bg-swiss-red-light px-3 py-1 text-sm text-swiss-red"
              >
                {s}
                <button
                  onClick={() => setSkills((prev) => prev.filter((x) => x !== s))}
                  className="ml-1 text-swiss-red hover:text-swiss-red-hover"
                >
                  x
                </button>
              </span>
            ))}
            {skills.length === 0 && (
              <p className="text-sm text-text-tertiary">No skills yet</p>
            )}
          </div>
          <form onSubmit={addSkill} className="flex gap-2">
            <input
              type="text"
              value={newSkill}
              onChange={(e) => setNewSkill(e.target.value)}
              placeholder="Add skill, language, tool, certification..."
              className="flex-1 rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-xl bg-swiss-red px-4 py-2 text-sm text-white hover:bg-swiss-red-hover"
            >
              Add
            </button>
          </form>
          <div className="mt-4 flex justify-between">
            <button
              onClick={() => setStep(0)}
              className="text-sm text-text-secondary hover:text-swiss-red"
            >
              Back
            </button>
            <button
              onClick={() => setStep(2)}
              className="rounded-xl bg-swiss-red px-4 py-2 text-sm font-semibold text-white hover:bg-swiss-red-hover"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Preferences */}
      {step === 2 && (
        <div className="bg-surface shadow-card rounded-2xl p-8">
          <h2 className="mb-2 text-lg font-medium text-text-primary">Preferences</h2>
          <p className="mb-4 text-sm text-text-secondary">
            Set your location, remote, and salary preferences.
          </p>

          {/* Locations */}
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
            Preferred Locations
          </label>
          <div className="mb-2 flex flex-wrap gap-2">
            {locations.map((loc) => (
              <span
                key={loc}
                className="inline-flex items-center gap-1 rounded-full bg-surface-tertiary px-3 py-1 text-sm text-text-primary"
              >
                {loc}
                <button
                  onClick={() => setLocations((prev) => prev.filter((l) => l !== loc))}
                  className="ml-1 text-text-tertiary hover:text-text-primary"
                >
                  x
                </button>
              </span>
            ))}
          </div>
          <form onSubmit={addLocation} className="mb-4 flex gap-2">
            <input
              type="text"
              value={newLoc}
              onChange={(e) => setNewLoc(e.target.value)}
              placeholder="e.g. Zurich"
              className="flex-1 rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-xl bg-swiss-red px-4 py-2 text-sm text-white hover:bg-swiss-red-hover"
            >
              Add
            </button>
          </form>

          {/* Remote */}
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
            Remote Preference
          </label>
          <select
            value={remotePref}
            onChange={(e) => setRemotePref(e.target.value)}
            className="mb-4 w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:outline-none"
          >
            {REMOTE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          {/* Salary */}
          <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
            Salary Range (CHF/year)
          </label>
          <div className="mb-4 flex gap-3">
            <input
              type="number"
              value={salaryMin}
              onChange={(e) => setSalaryMin(e.target.value)}
              placeholder="Min"
              className="w-1/2 rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:outline-none"
            />
            <input
              type="number"
              value={salaryMax}
              onChange={(e) => setSalaryMax(e.target.value)}
              placeholder="Max"
              className="w-1/2 rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:outline-none"
            />
          </div>

          <div className="flex justify-between">
            <button
              onClick={() => setStep(1)}
              className="text-sm text-text-secondary hover:text-swiss-red"
            >
              Back
            </button>
            <button
              onClick={() => setStep(3)}
              className="rounded-xl bg-swiss-red px-4 py-2 text-sm font-semibold text-white hover:bg-swiss-red-hover"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Ready */}
      {step === 3 && (
        <div className="bg-surface shadow-card rounded-2xl p-8 text-center">
          <div className="mb-4 flex justify-center">
            <svg className="h-12 w-12" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="24" className="fill-success" />
              <path d="M15 24l6 6 12-12" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h2 className="mb-2 text-lg font-medium text-text-primary">You're all set!</h2>
          <p className="mb-4 text-sm text-text-secondary">
            Your profile is ready. Let's find your perfect job matches.
          </p>
          <div className="mb-2 text-sm text-text-secondary">
            <p>{skills.length} skills configured</p>
            <p>{locations.length} preferred locations</p>
            {salaryMin && salaryMax && (
              <p>
                CHF {Number(salaryMin).toLocaleString()} -{" "}
                {Number(salaryMax).toLocaleString()}
              </p>
            )}
          </div>
          <button
            onClick={handleFinish}
            disabled={updateProfile.isPending}
            className="mt-4 w-full rounded-xl bg-swiss-red py-3 text-base font-semibold text-white shadow-card hover:bg-swiss-red-hover hover:shadow-card-hover transition-all disabled:opacity-50"
          >
            {updateProfile.isPending ? "Saving..." : "Find Matches"}
          </button>
          <button
            onClick={() => setStep(2)}
            className="mt-2 text-sm text-text-secondary hover:text-swiss-red"
          >
            Back
          </button>
        </div>
      )}
    </div>
  );
}
