import { lazy, Suspense } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import { useAuthHydration } from './hooks/useAuth'
import useAuthStore from './stores/authStore'
import Navbar from './components/Navbar'
import ProtectedRoute from './components/ProtectedRoute'
import { cn } from './components/ui'

const SearchPage = lazy(() => import('./pages/SearchPage'))
const JobDetailPage = lazy(() => import('./pages/JobDetailPage'))
const ProfilePage = lazy(() => import('./pages/ProfilePage'))
const MatchPage = lazy(() => import('./pages/MatchPage'))
const PipelinePage = lazy(() => import('./pages/PipelinePage'))
const SavedSearchesPage = lazy(() => import('./pages/SavedSearchesPage'))
const SavedJobsPage = lazy(() => import('./pages/SavedJobsPage'))
const FiltersPage = lazy(() => import('./pages/FiltersPage'))
const WatchlistPage = lazy(() => import('./pages/WatchlistPage'))
const OnboardingPage = lazy(() => import('./pages/OnboardingPage'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const RegisterPage = lazy(() => import('./pages/RegisterPage'))

function PageLoader() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-ink" />
    </div>
  )
}

function App() {
  useAuthHydration()
  const token = useAuthStore((s) => s.token)
  const location = useLocation()
  const onAuthPage =
    location.pathname === '/login' || location.pathname === '/register'

  // Compensa el bottom nav móvil para que el contenido no quede oculto.
  const hasBottomNav = !!token && !onAuthPage

  return (
    // min-h-screen-safe usa 100dvh para no romperse con la barra de URL
    // dinámica de Safari iOS.
    <div className="flex min-h-screen-safe flex-col bg-surface-secondary">
      <Navbar />
      <main
        className={cn(
          // px-safe respeta el notch lateral del iPhone en landscape.
          // No interfiere con el padding interno de cada página
          // (mx-auto + px-4) — solo añade inset en los bordes.
          'flex-1 px-safe',
          // pb-bottom-nav suma el alto de la BottomNav (5rem) más el
          // safe-area-inset-bottom del iPhone. En desktop se anula.
          hasBottomNav && 'pb-bottom-nav lg:pb-0',
        )}
      >
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<SearchPage />} />
            <Route path="/job/:hash" element={<JobDetailPage />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route
              path="/profile"
              element={
                <ProtectedRoute>
                  <ProfilePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/match"
              element={
                <ProtectedRoute>
                  <MatchPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/saved"
              element={
                <ProtectedRoute>
                  <SavedJobsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/pipeline"
              element={
                <ProtectedRoute>
                  <PipelinePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/searches"
              element={
                <ProtectedRoute>
                  <SavedSearchesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/filters"
              element={
                <ProtectedRoute>
                  <FiltersPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/watchlist"
              element={
                <ProtectedRoute>
                  <WatchlistPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/onboarding"
              element={
                <ProtectedRoute>
                  <OnboardingPage />
                </ProtectedRoute>
              }
            />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}

export default App
