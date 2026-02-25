import { Link } from "react-router-dom";
import useAuthStore from "../stores/authStore";
import { useLogout } from "../hooks/useAuth";

export default function Navbar() {
  const token = useAuthStore((s) => s.token);
  const logout = useLogout();

  return (
    <nav className="sticky top-0 z-40 border-b border-gray-200 bg-white">
      <div className="mx-auto flex h-12 max-w-2xl items-center justify-between px-4">
        <Link to="/" className="text-lg font-bold text-gray-900">
          SwissJob
        </Link>

        <div className="flex items-center gap-3">
          {token ? (
            <>
              <Link
                to="/profile"
                className="text-gray-600 hover:text-gray-900"
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
                className="text-sm text-gray-500 hover:text-gray-900"
              >
                Logout
              </button>
            </>
          ) : (
            <>
              <Link
                to="/login"
                className="text-sm text-gray-600 hover:text-gray-900"
              >
                Login
              </Link>
              <Link
                to="/register"
                className="rounded-lg bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
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
