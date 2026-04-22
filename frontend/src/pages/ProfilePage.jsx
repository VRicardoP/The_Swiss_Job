import { useState, useRef } from "react";
import { Link } from "react-router-dom";
import {
  useProfile,
  useUpdateProfile,
  useUploadCV,
  useDeleteCV,
} from "../hooks/useProfile";

const REMOTE_OPTIONS = [
  { value: "any", label: "Any" },
  { value: "remote_only", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "Onsite" },
];

const WEIGHT_KEYS = ["embedding", "llm", "salary", "location", "recency"];

function SkillTag({ skill, onRemove }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-swiss-red-light px-3 py-1 text-sm text-swiss-red">
      {skill}
      <button
        type="button"
        onClick={() => onRemove(skill)}
        className="ml-1 text-swiss-red hover:text-swiss-red-hover"
      >
        x
      </button>
    </span>
  );
}

export default function ProfilePage() {
  const { data: profile, isLoading, error } = useProfile();
  const updateProfile = useUpdateProfile();
  const uploadCV = useUploadCV();
  const deleteCV = useDeleteCV();
  const fileInputRef = useRef(null);

  const [form, setForm] = useState(null);
  const [newSkill, setNewSkill] = useState("");
  const [newLocation, setNewLocation] = useState("");

  // Initialize form from profile data once loaded
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

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <p className="text-error">Error loading profile: {error.message}</p>
        <Link to="/" className="text-swiss-red font-medium underline">
          Back to search
        </Link>
      </div>
    );
  }

  if (!form) return null;

  function handleChange(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function addSkill(e) {
    e.preventDefault();
    const s = newSkill.trim();
    if (s && !form.skills.includes(s)) {
      handleChange("skills", [...form.skills, s]);
    }
    setNewSkill("");
  }

  function removeSkill(skill) {
    handleChange(
      "skills",
      form.skills.filter((s) => s !== skill)
    );
  }

  function addLocation(e) {
    e.preventDefault();
    const loc = newLocation.trim();
    if (loc && !form.locations.includes(loc)) {
      handleChange("locations", [...form.locations, loc]);
    }
    setNewLocation("");
  }

  function removeLocation(loc) {
    handleChange(
      "locations",
      form.locations.filter((l) => l !== loc)
    );
  }

  function handleWeightChange(key, value) {
    const numVal = parseFloat(value);
    if (isNaN(numVal)) return;
    setForm((prev) => ({
      ...prev,
      score_weights: { ...prev.score_weights, [key]: numVal },
    }));
  }

  async function handleSave() {
    const data = {
      title: form.title || null,
      skills: form.skills,
      locations: form.locations,
      salary_min: form.salary_min ? Number(form.salary_min) : null,
      salary_max: form.salary_max ? Number(form.salary_max) : null,
      remote_pref: form.remote_pref,
      score_weights: form.score_weights,
    };
    updateProfile.mutate(data);
  }

  async function handleUploadCV(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    uploadCV.mutate(file);
  }

  const weightsSum = Object.values(form.score_weights).reduce(
    (a, b) => a + b,
    0
  );
  const weightsValid = Math.abs(weightsSum - 1.0) <= 0.02;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 pb-20">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-primary tracking-tight">My Profile</h1>
        <Link to="/" className="text-sm text-swiss-red font-medium hover:underline">
          Back to search
        </Link>
      </div>

      {/* Title */}
      <section className="bg-surface shadow-card rounded-xl p-6 mb-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Job Title / Role
        </label>
        <input
          type="text"
          value={form.title}
          onChange={(e) => handleChange("title", e.target.value)}
          placeholder="e.g. Content Editor, HR Coordinator, AI Evaluator"
          className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none"
        />
      </section>

      {/* Skills */}
      <section className="bg-surface shadow-card rounded-xl p-6 mb-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Skills, Languages & Certifications
        </label>
        <div className="mb-2 flex flex-wrap gap-2">
          {form.skills.map((s) => (
            <SkillTag key={s} skill={s} onRemove={removeSkill} />
          ))}
        </div>
        <form onSubmit={addSkill} className="flex gap-2">
          <input
            type="text"
            value={newSkill}
            onChange={(e) => setNewSkill(e.target.value)}
            placeholder="Add skill, language, tool, certification..."
            className="flex-1 rounded-xl border border-border bg-surface-secondary px-3 py-2 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-xl bg-swiss-red px-4 py-2 text-white hover:bg-swiss-red-hover"
          >
            Add
          </button>
        </form>
      </section>

      {/* Locations */}
      <section className="bg-surface shadow-card rounded-xl p-6 mb-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Preferred Locations
        </label>
        <div className="mb-2 flex flex-wrap gap-2">
          {form.locations.map((loc) => (
            <span
              key={loc}
              className="inline-flex items-center gap-1 rounded-full bg-surface-tertiary px-3 py-1 text-sm text-text-primary"
            >
              {loc}
              <button
                type="button"
                onClick={() => removeLocation(loc)}
                className="ml-1 text-text-tertiary hover:text-text-primary"
              >
                x
              </button>
            </span>
          ))}
        </div>
        <form onSubmit={addLocation} className="flex gap-2">
          <input
            type="text"
            value={newLocation}
            onChange={(e) => setNewLocation(e.target.value)}
            placeholder="e.g. Zurich"
            className="flex-1 rounded-xl border border-border bg-surface-secondary px-3 py-2 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-xl bg-swiss-red px-4 py-2 text-white hover:bg-swiss-red-hover"
          >
            Add
          </button>
        </form>
      </section>

      {/* Salary Range */}
      <section className="bg-surface shadow-card rounded-xl p-6 mb-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Salary Range (CHF/year)
        </label>
        <div className="flex gap-3">
          <input
            type="number"
            value={form.salary_min}
            onChange={(e) => handleChange("salary_min", e.target.value)}
            placeholder="Min"
            className="w-1/2 rounded-xl border border-border bg-surface-secondary px-3 py-2 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none"
          />
          <input
            type="number"
            value={form.salary_max}
            onChange={(e) => handleChange("salary_max", e.target.value)}
            placeholder="Max"
            className="w-1/2 rounded-xl border border-border bg-surface-secondary px-3 py-2 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none"
          />
        </div>
      </section>

      {/* Remote Preference */}
      <section className="bg-surface shadow-card rounded-xl p-6 mb-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Remote Preference
        </label>
        <select
          value={form.remote_pref}
          onChange={(e) => handleChange("remote_pref", e.target.value)}
          className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none"
        >
          {REMOTE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </section>

      {/* Score Weights */}
      <section className="bg-surface shadow-card rounded-xl p-6 mb-6">
        <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
          Match Score Weights
        </label>
        <p className="mb-2 text-xs text-text-secondary">
          Adjust how matches are scored. Must sum to 1.0.
        </p>
        {WEIGHT_KEYS.map((key) => (
          <div key={key} className="mb-2 flex items-center gap-3">
            <span className="w-24 text-sm capitalize text-text-secondary">
              {key}
            </span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={form.score_weights[key] ?? 0}
              onChange={(e) => handleWeightChange(key, e.target.value)}
              className="flex-1 accent-swiss-red"
            />
            <span className="w-12 text-right text-sm text-text-primary">
              {(form.score_weights[key] ?? 0).toFixed(2)}
            </span>
          </div>
        ))}
        <p
          className={`text-xs ${weightsValid ? "text-success" : "text-error"}`}
        >
          Sum: {weightsSum.toFixed(2)} {weightsValid ? "" : "(must be ~1.0)"}
        </p>
      </section>

      {/* CV Upload */}
      <section className="bg-surface-secondary rounded-xl p-6 mb-6">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-text-tertiary">CV / Resume</h2>
        {profile.cv_text ? (
          <div className="mb-3">
            <p className="text-sm text-success">
              CV uploaded ({profile.cv_text.length.toLocaleString()} chars)
              {profile.has_cv_embedding && " - Embedding generated"}
            </p>
            <button
              type="button"
              onClick={() => deleteCV.mutate()}
              disabled={deleteCV.isPending}
              className="mt-2 text-sm text-error hover:text-error/80 disabled:opacity-50"
            >
              {deleteCV.isPending ? "Deleting..." : "Delete CV"}
            </button>
          </div>
        ) : (
          <p className="mb-3 text-sm text-text-secondary">No CV uploaded yet.</p>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx"
          onChange={handleUploadCV}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploadCV.isPending}
          className="rounded-xl bg-swiss-red px-4 py-2 text-sm text-white hover:bg-swiss-red-hover disabled:opacity-50"
        >
          {uploadCV.isPending ? "Uploading..." : "Upload CV (PDF/DOCX)"}
        </button>
        {uploadCV.isSuccess && (
          <p className="mt-2 text-sm text-success">
            CV uploaded! Extracted {uploadCV.data.skills_extracted.length}{" "}
            skills.
          </p>
        )}
        {uploadCV.isError && (
          <p className="mt-2 text-sm text-error">
            Upload failed: {uploadCV.error.message}
          </p>
        )}
      </section>

      {/* Save Button */}
      <button
        type="button"
        onClick={handleSave}
        disabled={updateProfile.isPending || !weightsValid}
        className="w-full rounded-xl bg-swiss-red py-3 text-base font-semibold text-white shadow-card hover:bg-swiss-red-hover hover:shadow-card-hover transition-all duration-200 disabled:opacity-50"
      >
        {updateProfile.isPending ? "Saving..." : "Save Profile"}
      </button>

      {updateProfile.isSuccess && (
        <p className="mt-2 text-center text-sm text-success">
          Profile saved successfully.
        </p>
      )}
      {updateProfile.isError && (
        <p className="mt-2 text-center text-sm text-error">
          Error: {updateProfile.error.message}
        </p>
      )}
    </div>
  );
}
