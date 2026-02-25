import { useState, useRef } from "react";
import { Link } from "react-router-dom";
import {
  useProfile,
  useUpdateProfile,
  useUploadCV,
  useDeleteCV,
} from "../hooks/useProfile";

const REMOTE_OPTIONS = ["any", "remote", "hybrid", "onsite"];

const WEIGHT_KEYS = ["embedding", "llm", "salary", "location", "recency"];

function SkillTag({ skill, onRemove }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-3 py-1 text-sm text-blue-800">
      {skill}
      <button
        type="button"
        onClick={() => onRemove(skill)}
        className="ml-1 text-blue-500 hover:text-blue-700"
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
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <p className="text-red-600">Error loading profile: {error.message}</p>
        <Link to="/" className="text-blue-600 underline">
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
    <div className="mx-auto max-w-2xl p-4 pb-20">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">My Profile</h1>
        <Link to="/" className="text-sm text-blue-600 hover:underline">
          Back to search
        </Link>
      </div>

      {/* Title */}
      <section className="mb-6">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Job Title / Role
        </label>
        <input
          type="text"
          value={form.title}
          onChange={(e) => handleChange("title", e.target.value)}
          placeholder="e.g. Senior Backend Developer"
          className="w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
        />
      </section>

      {/* Skills */}
      <section className="mb-6">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Skills
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
            placeholder="Add skill..."
            className="flex-1 rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-lg bg-blue-500 px-4 py-2 text-white hover:bg-blue-600"
          >
            Add
          </button>
        </form>
      </section>

      {/* Locations */}
      <section className="mb-6">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Preferred Locations
        </label>
        <div className="mb-2 flex flex-wrap gap-2">
          {form.locations.map((loc) => (
            <span
              key={loc}
              className="inline-flex items-center gap-1 rounded-full bg-green-100 px-3 py-1 text-sm text-green-800"
            >
              {loc}
              <button
                type="button"
                onClick={() => removeLocation(loc)}
                className="ml-1 text-green-500 hover:text-green-700"
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
            className="flex-1 rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
          />
          <button
            type="submit"
            className="rounded-lg bg-green-500 px-4 py-2 text-white hover:bg-green-600"
          >
            Add
          </button>
        </form>
      </section>

      {/* Salary Range */}
      <section className="mb-6">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Salary Range (CHF/year)
        </label>
        <div className="flex gap-3">
          <input
            type="number"
            value={form.salary_min}
            onChange={(e) => handleChange("salary_min", e.target.value)}
            placeholder="Min"
            className="w-1/2 rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
          />
          <input
            type="number"
            value={form.salary_max}
            onChange={(e) => handleChange("salary_max", e.target.value)}
            placeholder="Max"
            className="w-1/2 rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
          />
        </div>
      </section>

      {/* Remote Preference */}
      <section className="mb-6">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Remote Preference
        </label>
        <select
          value={form.remote_pref}
          onChange={(e) => handleChange("remote_pref", e.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
        >
          {REMOTE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt.charAt(0).toUpperCase() + opt.slice(1)}
            </option>
          ))}
        </select>
      </section>

      {/* Score Weights */}
      <section className="mb-6">
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Match Score Weights
        </label>
        <p className="mb-2 text-xs text-gray-500">
          Adjust how matches are scored. Must sum to 1.0.
        </p>
        {WEIGHT_KEYS.map((key) => (
          <div key={key} className="mb-2 flex items-center gap-3">
            <span className="w-24 text-sm capitalize text-gray-600">
              {key}
            </span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={form.score_weights[key] ?? 0}
              onChange={(e) => handleWeightChange(key, e.target.value)}
              className="flex-1"
            />
            <span className="w-12 text-right text-sm text-gray-700">
              {(form.score_weights[key] ?? 0).toFixed(2)}
            </span>
          </div>
        ))}
        <p
          className={`text-xs ${weightsValid ? "text-green-600" : "text-red-600"}`}
        >
          Sum: {weightsSum.toFixed(2)} {weightsValid ? "" : "(must be ~1.0)"}
        </p>
      </section>

      {/* CV Upload */}
      <section className="mb-6 rounded-lg border border-gray-200 bg-gray-50 p-4">
        <h2 className="mb-2 text-sm font-medium text-gray-700">CV / Resume</h2>
        {profile.cv_text ? (
          <div className="mb-3">
            <p className="text-sm text-green-700">
              CV uploaded ({profile.cv_text.length.toLocaleString()} chars)
              {profile.has_cv_embedding && " - Embedding generated"}
            </p>
            <button
              type="button"
              onClick={() => deleteCV.mutate()}
              disabled={deleteCV.isPending}
              className="mt-2 text-sm text-red-600 hover:underline disabled:opacity-50"
            >
              {deleteCV.isPending ? "Deleting..." : "Delete CV"}
            </button>
          </div>
        ) : (
          <p className="mb-3 text-sm text-gray-500">No CV uploaded yet.</p>
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
          className="rounded-lg bg-gray-200 px-4 py-2 text-sm text-gray-700 hover:bg-gray-300 disabled:opacity-50"
        >
          {uploadCV.isPending ? "Uploading..." : "Upload CV (PDF/DOCX)"}
        </button>
        {uploadCV.isSuccess && (
          <p className="mt-2 text-sm text-green-600">
            CV uploaded! Extracted {uploadCV.data.skills_extracted.length}{" "}
            skills.
          </p>
        )}
        {uploadCV.isError && (
          <p className="mt-2 text-sm text-red-600">
            Upload failed: {uploadCV.error.message}
          </p>
        )}
      </section>

      {/* Save Button */}
      <button
        type="button"
        onClick={handleSave}
        disabled={updateProfile.isPending || !weightsValid}
        className="w-full rounded-lg bg-blue-600 py-3 text-white font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        {updateProfile.isPending ? "Saving..." : "Save Profile"}
      </button>

      {updateProfile.isSuccess && (
        <p className="mt-2 text-center text-sm text-green-600">
          Profile saved successfully.
        </p>
      )}
      {updateProfile.isError && (
        <p className="mt-2 text-center text-sm text-red-600">
          Error: {updateProfile.error.message}
        </p>
      )}
    </div>
  );
}
