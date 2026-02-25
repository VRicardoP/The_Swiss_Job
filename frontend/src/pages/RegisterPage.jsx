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
    <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-center text-2xl font-bold text-gray-900">
          Create your account
        </h1>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-lg border border-gray-200 bg-white p-6"
        >
          {register.isError && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {register.error.message}
            </div>
          )}

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-blue-500 focus:outline-none"
              placeholder="Min 8 characters"
            />
          </div>

          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              checked={gdprConsent}
              onChange={(e) => setGdprConsent(e.target.checked)}
              className="mt-0.5 rounded border-gray-300"
            />
            <span className="text-sm text-gray-600">
              I consent to the processing of my personal data in accordance with
              GDPR regulations.
            </span>
          </label>

          <button
            type="submit"
            disabled={!canSubmit}
            className="w-full rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {register.isPending ? "Creating account..." : "Create account"}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-gray-600">
          Already have an account?{" "}
          <Link to="/login" className="text-blue-600 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
