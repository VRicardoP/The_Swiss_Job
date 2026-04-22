import { Link } from "react-router-dom";
import useAuthStore from "../stores/authStore";
import { useLogout } from "../hooks/useAuth";
import NotificationBell from "./NotificationBell";

export default function Navbar() {
  const token = useAuthStore((s) => s.token);
  const logout = useLogout();

  return (
    <nav className="sticky top-0 z-40 h-16 bg-surface/80 backdrop-blur-lg border-b border-border shadow-xs">
      <div className="mx-auto flex h-full max-w-6xl items-center justify-between px-4">
        <Link to="/" className="flex items-center gap-2 text-lg font-bold text-text-primary">
          <svg className="h-7 w-7" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="6" className="fill-swiss-red" />
            <path d="M10 16h12M16 10v12" stroke="white" strokeWidth="3.5" strokeLinecap="round" />
          </svg>
          SwissJob
        </Link>

        <div className="flex items-center gap-3">
          {token ? (
            <>
              <Link
                to="/match"
                className="text-sm text-text-secondary hover:text-swiss-red transition-colors duration-200"
              >
                Matches
              </Link>
              <Link
                to="/saved"
                className="text-sm text-text-secondary hover:text-swiss-red transition-colors duration-200"
              >
                Saved
              </Link>
              <Link
                to="/pipeline"
                className="text-sm text-text-secondary hover:text-swiss-red transition-colors duration-200"
              >
                Pipeline
              </Link>
              <Link
                to="/searches"
                className="text-sm text-text-secondary hover:text-swiss-red transition-colors duration-200"
              >
                Alerts
              </Link>
              <NotificationBell />
              <Link
                to="/profile"
                className="w-8 h-8 rounded-full bg-surface-tertiary flex items-center justify-center hover:bg-swiss-red-light transition-colors duration-200"
              >
                <svg
                  className="h-5 w-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                  />
                </svg>
              </Link>
              <button
                onClick={logout}
                className="text-sm text-text-tertiary hover:text-swiss-red transition-colors duration-200"
              >
                Logout
              </button>
            </>
          ) : (
            <>
              <Link
                to="/login"
                className="text-sm text-text-secondary hover:text-swiss-red transition-colors"
              >
                Login
              </Link>
              <Link
                to="/register"
                className="bg-swiss-red hover:bg-swiss-red-hover text-white rounded-full px-5 py-2 text-sm font-semibold transition-all duration-200 shadow-xs"
              >
                Register
              </Link>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
