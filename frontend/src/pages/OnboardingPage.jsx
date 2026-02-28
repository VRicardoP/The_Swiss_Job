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
                ? "bg-green-500 text-white"
                : i === current
                  ? "bg-blue-600 text-white"
                  : "bg-gray-200 text-gray-500"
            }`}
          >
            {i < current ? "\u2713" : i + 1}
          </div>
          <span
            className={`hidden text-xs sm:inline ${
              i === current ? "font-medium text-gray-900" : "text-gray-400"
            }`}
          >
            {label}
          </span>
          {i < STEPS.length - 1 && (
            <div className="h-px w-6 bg-gray-300" />
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
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
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
      <h1 className="mb-2 text-center text-2xl font-bold text-gray-900">
        Welcome to SwissJob
      </h1>
      <p className="mb-6 text-center text-sm text-gray-500">
        Let's set up your profile to find the best matches
      </p>

      <StepIndicator current={step} />

      {/* Step 0: Upload CV */}
      {step === 0 && (
        <div className="rounded-lg border border-gray-200 p-6 text-center">
          <h2 className="mb-2 text-lg font-medium text-gray-800">Upload your CV</h2>
          <p className="mb-4 text-sm text-gray-500">
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
            className="rounded-lg bg-blue-600 px-6 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {uploadCV.isPending ? "Uploading..." : "Choose File (PDF/DOCX)"}
          </button>
          {cvUploaded && (
            <p className="mt-3 text-sm text-green-600">CV uploaded successfully!</p>
          )}
          {uploadCV.isError && (
            <p className="mt-3 text-sm text-red-600">{uploadCV.error.message}</p>
          )}
          <div className="mt-4">
            <button
              onClick={() => setStep(1)}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              {cvUploaded ? "Next" : "Skip for now"}
            </button>
          </div>
        </div>
      )}

      {/* Step 1: Confirm Skills */}
      {step === 1 && (
        <div className="rounded-lg border border-gray-200 p-6">
          <h2 className="mb-2 text-lg font-medium text-gray-800">Confirm your skills</h2>
          <p className="mb-4 text-sm text-gray-500">
            Add or remove skills to improve your match quality.
          </p>
          <div className="mb-3 flex flex-wrap gap-2">
            {skills.map((s) => (
              <span
                key={s}
                className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-3 py-1 text-sm text-blue-800"
              >
                {s}
                <button
                  onClick={() => setSkills((prev) => prev.filter((x) => x !== s))}
                  className="ml-1 text-blue-500 hover:text-blue-700"
                >
                  x
                </button>
              </span>
            ))}
            {skills.length === 0 && (
              <p className="text-sm text-gray-400">No skills yet</p>
            )}
          </div>
          <form onSubmit={addSkill} className="flex gap-2">
            <input
              type="text"
              value={newSkill}
              onChange={(e) => setNewSkill(e.target.value)}
              placeholder="Add skill..."
              className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-lg bg-blue-500 px-4 py-2 text-sm text-white hover:bg-blue-600"
            >
              Add
            </button>
          </form>
          <div className="mt-4 flex justify-between">
            <button
              onClick={() => setStep(0)}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Back
            </button>
            <button
              onClick={() => setStep(2)}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Preferences */}
      {step === 2 && (
        <div className="rounded-lg border border-gray-200 p-6">
          <h2 className="mb-2 text-lg font-medium text-gray-800">Preferences</h2>
          <p className="mb-4 text-sm text-gray-500">
            Set your location, remote, and salary preferences.
          </p>

          {/* Locations */}
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Preferred Locations
          </label>
          <div className="mb-2 flex flex-wrap gap-2">
            {locations.map((loc) => (
              <span
                key={loc}
                className="inline-flex items-center gap-1 rounded-full bg-green-100 px-3 py-1 text-sm text-green-800"
              >
                {loc}
                <button
                  onClick={() => setLocations((prev) => prev.filter((l) => l !== loc))}
                  className="ml-1 text-green-500 hover:text-green-700"
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
              className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
            />
            <button
              type="submit"
              className="rounded-lg bg-green-500 px-4 py-2 text-sm text-white hover:bg-green-600"
            >
              Add
            </button>
          </form>

          {/* Remote */}
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Remote Preference
          </label>
          <select
            value={remotePref}
            onChange={(e) => setRemotePref(e.target.value)}
            className="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          >
            {REMOTE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          {/* Salary */}
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Salary Range (CHF/year)
          </label>
          <div className="mb-4 flex gap-3">
            <input
              type="number"
              value={salaryMin}
              onChange={(e) => setSalaryMin(e.target.value)}
              placeholder="Min"
              className="w-1/2 rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
            <input
              type="number"
              value={salaryMax}
              onChange={(e) => setSalaryMax(e.target.value)}
              placeholder="Max"
              className="w-1/2 rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>

          <div className="flex justify-between">
            <button
              onClick={() => setStep(1)}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Back
            </button>
            <button
              onClick={() => setStep(3)}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Ready */}
      {step === 3 && (
        <div className="rounded-lg border border-gray-200 p-6 text-center">
          <div className="mb-4 text-4xl">&#127881;</div>
          <h2 className="mb-2 text-lg font-medium text-gray-800">You're all set!</h2>
          <p className="mb-4 text-sm text-gray-500">
            Your profile is ready. Let's find your perfect job matches.
          </p>
          <div className="mb-2 text-sm text-gray-600">
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
            className="mt-4 w-full rounded-lg bg-blue-600 py-3 text-white font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {updateProfile.isPending ? "Saving..." : "Find Matches"}
          </button>
          <button
            onClick={() => setStep(2)}
            className="mt-2 text-sm text-gray-500 hover:text-gray-700"
          >
            Back
          </button>
        </div>
      )}
    </div>
  );
}
