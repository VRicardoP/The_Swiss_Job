import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useLogin } from "../hooks/useAuth";
import useAuthStore from "../stores/authStore";

export default function LoginPage() {
  const token = useAuthStore((s) => s.token);
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  if (token) return <Navigate to="/profile" replace />;

  function handleSubmit(e) {
    e.preventDefault();
    if (password.length < 8) return;
    login.mutate({ email, password });
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
          Sign in to SwissJob
        </h1>
        <p className="mb-6 text-center text-sm text-text-secondary">
          Welcome back
        </p>

        <form
          onSubmit={handleSubmit}
          className="space-y-5 bg-surface shadow-card rounded-2xl p-8"
        >
          {login.isError && (
            <div className="rounded-xl bg-error-light p-3 text-sm text-error">
              {login.error.message}
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

          <button
            type="submit"
            disabled={login.isPending || !email || password.length < 8}
            className="w-full rounded-xl bg-swiss-red hover:bg-swiss-red-hover px-4 py-3 text-sm font-semibold text-white shadow-xs hover:shadow-card transition-all duration-200 disabled:opacity-50"
          >
            {login.isPending ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-text-secondary">
          No account?{" "}
          <Link to="/register" className="text-swiss-red font-medium hover:underline">
            Register
          </Link>
        </p>
      </div>
    </div>
  );
}
