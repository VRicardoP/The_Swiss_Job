import { Routes, Route } from 'react-router-dom'
import SearchPage from './pages/SearchPage'
import JobDetailPage from './pages/JobDetailPage'

function App() {
  return (
    <Routes>
      <Route path="/" element={<SearchPage />} />
      <Route path="/job/:hash" element={<JobDetailPage />} />
    </Routes>
  )
}

export default App
