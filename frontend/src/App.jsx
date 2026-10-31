import { Routes, Route } from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import Home from './pages/Home.jsx'
import RecordMeeting from './pages/RecordMeeting.jsx'
import Transcript from './pages/Transcript.jsx'
import Notes from './pages/Notes.jsx'
import Reports from './pages/Reports.jsx'
import History from './pages/History.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Login from './pages/Login.jsx'
import { RecordingProvider } from './context/RecordingContext.jsx'

export default function App() {
  return (
    // Mounted here (inside BrowserRouter, above <Routes>) rather than in
    // main.jsx: it needs useNavigate, and placing it above <Routes> means
    // it survives every internal navigation, so an in-progress recording
    // (and its live mic stream, streaming-chunk loop, captions) outlives
    // the /record page being unmounted instead of being torn down with it.
    <RecordingProvider>
      <div className="app-shell">
        <Navbar />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/record" element={<RecordMeeting />} />
            <Route path="/transcript" element={<Transcript />} />
            <Route path="/notes" element={<Notes />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/history" element={<History />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/login" element={<Login />} />
          </Routes>
        </main>
      </div>
    </RecordingProvider>
  )
}