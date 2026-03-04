import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useRegister } from "../hooks/useAuth";
import useAuthStore from "../stores/authStore";

export default function RegisterPage() {
  const token = useAuthStore((s) => s.token);
  const register = useRegister();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [gdprConsent, setGdprConsent] = useState(false);

  if (token) return <Navigate to="/profile" replace />;

  const canSubmit =
    email && password.length >= 8 && gdprConsent && !register.isPending;

  function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    register.mutate({ email, password, gdpr_consent: true });
  }

  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-6">
          <svg className="h-12 w-12" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="6" className="fill-swiss-red" />
            <path d="M10 16h12M16 10v12" stroke="white" strokeWidth="3.5" strokeLinecap="round" />
          </svg>
        </div>

        <h1 className="mb-2 text-center text-2xl font-bold text-text-primary tracking-tight">
          Create your account
        </h1>
        <p className="mb-6 text-center text-sm text-text-secondary">
          Get started with SwissJob
        </p>

        <form
          onSubmit={handleSubmit}
          className="space-y-5 bg-surface shadow-card rounded-2xl p-8"
        >
          {register.isError && (
            <div className="rounded-xl bg-error-light p-3 text-sm text-error">
              {register.error.message}
            </div>
          )}

          <div>
            <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full rounded-xl border border-border bg-surface-secondary px-4 py-3 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none transition-all"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="w-full rounded-xl border border-border bg-surface-secondary px-4 py-3 focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 focus:outline-none transition-all"
              placeholder="Min 8 characters"
            />
          </div>

          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              checked={gdprConsent}
              onChange={(e) => setGdprConsent(e.target.checked)}
              className="mt-0.5 rounded border-border accent-swiss-red"
            />
            <span className="text-sm text-text-secondary">
              I consent to the processing of my personal data in accordance with
              GDPR regulations.
            </span>
          </label>

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full rounded-xl bg-swiss-red hover:bg-swiss-red-hover px-4 py-3 text-sm font-semibold text-white shadow-xs hover:shadow-card transition-all duration-200 disabled:opacity-50"
          >
            {register.isPending ? "Creating account..." : "Create account"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-text-secondary">
          Already have an account?{" "}
          <Link to="/login" className="text-swiss-red font-medium hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
