import { lazy, Suspense } from 'react'
import { Routes, Route } from 'react-router-dom'
import { useAuthHydration } from './hooks/useAuth'
import Navbar from './components/Navbar'
import ProtectedRoute from './components/ProtectedRoute'

const SearchPage = lazy(() => import('./pages/SearchPage'))
const JobDetailPage = lazy(() => import('./pages/JobDetailPage'))
const ProfilePage = lazy(() => import('./pages/ProfilePage'))
const MatchPage = lazy(() => import('./pages/MatchPage'))
const PipelinePage = lazy(() => import('./pages/PipelinePage'))
const SavedSearchesPage = lazy(() => import('./pages/SavedSearchesPage'))
const OnboardingPage = lazy(() => import('./pages/OnboardingPage'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const RegisterPage = lazy(() => import('./pages/RegisterPage'))

function PageLoader() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-gray-900" />
    </div>
  )
}

function App() {
  useAuthHydration()

  return (
    <>
      <Navbar />
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
            path="/onboarding"
            element={
              <ProtectedRoute>
                <OnboardingPage />
              </ProtectedRoute>
            }
          />
        </Routes>
      </Suspense>
    </>
  )
}

export default App
