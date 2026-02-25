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
    <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-center text-2xl font-bold text-gray-900">
          Sign in to SwissJob
        </h1>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-lg border border-gray-200 bg-white p-6"
        >
          {login.isError && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {login.error.message}
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

          <button
            type="submit"
            disabled={login.isPending || !email || password.length < 8}
            className="w-full rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {login.isPending ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-gray-600">
          No account?{" "}
          <Link to="/register" className="text-blue-600 hover:underline">
            Register
          </Link>
        </p>
      </div>
    </div>
  );
}
