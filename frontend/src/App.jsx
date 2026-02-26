import { Routes, Route } from 'react-router-dom'
import { useAuthHydration } from './hooks/useAuth'
import Navbar from './components/Navbar'
import ProtectedRoute from './components/ProtectedRoute'
import SearchPage from './pages/SearchPage'
import JobDetailPage from './pages/JobDetailPage'
import ProfilePage from './pages/ProfilePage'
import MatchPage from './pages/MatchPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'

function App() {
  useAuthHydration()

  return (
    <>
      <Navbar />
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
      </Routes>
    </>
  )
}

export default App
