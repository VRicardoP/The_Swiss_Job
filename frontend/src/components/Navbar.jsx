import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Search,
  Sparkles,
  Bookmark,
  Kanban,
  Bell,
  User,
  LogOut,
  Settings,
  SlidersHorizontal,
  ChevronDown,
  GraduationCap,
} from "lucide-react";
import useAuthStore from "../stores/authStore";
import { useLogout } from "../hooks/useAuth";
import NotificationBell from "./NotificationBell";
import { Button, Avatar, cn } from "./ui";

// Rutas principales (mostradas como tabs en desktop y bottom nav en móvil)
const PRIMARY_NAV = [
  { to: "/",         label: "Search",   icon: Search,    end: true },
  { to: "/match",    label: "Matches",  icon: Sparkles },
  { to: "/saved",    label: "Saved",    icon: Bookmark },
  { to: "/pipeline", label: "Pipeline", icon: Kanban },
];

// Rutas secundarias (menú móvil + acciones desktop)
const SECONDARY_NAV = [
  { to: "/searches",  label: "Alerts",    icon: Bell },
  { to: "/watchlist", label: "Watchlist", icon: GraduationCap },
  { to: "/filters",   label: "Filters",   icon: SlidersHorizontal },
];

function BrandMark() {
  return (
    <Link
      to="/"
      className="flex items-center gap-2 text-base font-semibold tracking-tight text-text-primary"
    >
      <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-text-inverse">
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="2" y="2" width="20" height="20" rx="3" className="fill-swiss-red" />
          <path d="M7 12h10M12 7v10" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
      </span>
      <span>SwissJob</span>
    </Link>
  );
}

function DesktopTab({ to, label, icon: Icon, end }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "relative inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium tracking-tight transition-all duration-150",
          isActive
            ? "bg-swiss-red-50 text-swiss-red"
            : "text-text-secondary hover:bg-surface-tertiary hover:text-text-primary",
        )
      }
    >
      {Icon && <Icon className="h-4 w-4" aria-hidden="true" />}
      {label}
    </NavLink>
  );
}

function UserMenu({ user, onLogout }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full p-0.5 pr-2 transition-all duration-150",
          "hover:bg-surface-tertiary",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        )}
      >
        <Avatar name={user?.email || "U"} size="sm" shape="circle" />
        <ChevronDown className="h-3.5 w-3.5 text-text-tertiary" aria-hidden="true" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-11 z-50 w-60 origin-top-right animate-scale-in rounded-xl border border-border bg-surface p-1.5 shadow-dropdown"
        >
          <div className="px-2.5 py-2">
            <p className="text-xs text-text-tertiary">Signed in as</p>
            <p className="truncate text-sm font-medium text-text-primary">
              {user?.email || "—"}
            </p>
          </div>
          <div className="my-1 h-px bg-border-light" />

          <MenuItem to="/profile" icon={User} label="Profile" onClick={() => setOpen(false)} />
          <MenuItem to="/searches" icon={Bell} label="Saved alerts" onClick={() => setOpen(false)} />
          <MenuItem to="/watchlist" icon={GraduationCap} label="Watchlist" onClick={() => setOpen(false)} />
          <MenuItem to="/filters" icon={SlidersHorizontal} label="Filters" onClick={() => setOpen(false)} />
          <MenuItem to="/onboarding" icon={Settings} label="Onboarding" onClick={() => setOpen(false)} />

          <div className="my-1 h-px bg-border-light" />
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onLogout();
            }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-sm text-text-secondary hover:bg-error-light hover:text-error transition-colors"
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

function MenuItem({ to, icon: Icon, label, onClick }) {
  return (
    <Link
      to={to}
      onClick={onClick}
      role="menuitem"
      className="flex items-center gap-2 rounded-md px-2.5 py-2 text-sm text-text-primary hover:bg-surface-tertiary transition-colors"
    >
      {Icon && <Icon className="h-4 w-4 text-text-secondary" aria-hidden="true" />}
      {label}
    </Link>
  );
}

function BottomNavItem({ to, end, label, icon: Icon }) {
  return (
    <li className="flex-1">
      <NavLink
        to={to}
        end={end}
        className={({ isActive }) =>
          cn(
            "flex flex-col items-center justify-center gap-0.5 py-2.5 text-[11px] font-medium",
            "transition-colors duration-150",
            isActive ? "text-swiss-red" : "text-text-tertiary",
          )
        }
      >
        {({ isActive }) => (
          <>
            <span
              className={cn(
                "flex h-7 w-12 items-center justify-center rounded-full transition-all duration-150",
                isActive && "bg-swiss-red-50",
              )}
            >
              {Icon && (
                <Icon
                  className={cn(
                    "h-5 w-5 transition-colors",
                    isActive ? "text-swiss-red" : "text-text-tertiary",
                  )}
                  aria-hidden="true"
                  // Cuando un item se activa, engrosamos un poco el trazo para
                  // que la diferencia con los inactivos sea más obvia en
                  // pantallas pequeñas y a un vistazo rápido.
                  strokeWidth={isActive ? 2.4 : 2}
                />
              )}
            </span>
            <span>{label}</span>
          </>
        )}
      </NavLink>
    </li>
  );
}

function BottomNav() {
  return (
    <nav
      // pb-safe respeta el home indicator; px-safe el notch lateral
      // cuando el iPhone se gira a landscape.
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface/95 backdrop-blur-lg pb-safe px-safe lg:hidden"
      aria-label="Primary"
    >
      <ul className="mx-auto flex max-w-2xl items-stretch">
        {PRIMARY_NAV.map((item) => (
          <BottomNavItem key={item.to} {...item} />
        ))}
        <BottomNavItem to="/profile" label="Profile" icon={User} />
      </ul>
    </nav>
  );
}

export default function Navbar() {
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const logout = useLogout();
  const location = useLocation();
  const navigate = useNavigate();

  // En vistas de auth (login/register) la navbar se reduce
  const onAuthPage = location.pathname === "/login" || location.pathname === "/register";

  return (
    <>
      {/* pt-safe: empuja el header bajo el Dynamic Island / notch.
          px-safe-plus-4: añade el inset lateral cuando el iPhone está
          en landscape, sin sacrificar el padding mínimo de 1rem. */}
      <header className="sticky top-0 z-40 border-b border-border bg-surface/85 backdrop-blur-lg pt-safe">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-safe-plus-4 sm:h-16 sm:px-safe-plus-6">
          <BrandMark />

          {token && !onAuthPage && (
            <nav className="ml-2 hidden items-center gap-0.5 lg:flex" aria-label="Primary">
              {PRIMARY_NAV.map((item) => (
                <DesktopTab key={item.to} {...item} />
              ))}
            </nav>
          )}

          <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
            {token ? (
              <>
                <nav className="hidden items-center gap-0.5 lg:flex" aria-label="Secondary">
                  {SECONDARY_NAV.map((item) => (
                    <DesktopTab key={item.to} {...item} />
                  ))}
                </nav>

                <NotificationBell />

                <UserMenu user={user} onLogout={() => logout()} />
              </>
            ) : (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => navigate("/login")}
                  className="hidden sm:inline-flex"
                >
                  Log in
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => navigate("/register")}
                >
                  Get started
                </Button>
              </>
            )}
          </div>
        </div>
      </header>

      {token && !onAuthPage && <BottomNav />}
    </>
  );
}
