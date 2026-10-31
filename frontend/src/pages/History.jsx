import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMeeting } from '../context/MeetingContext.jsx'
import { useAuth, authFetch } from '../context/AuthContext.jsx'
import { API_BASE } from '../config/api.js'

function formatDuration(totalSeconds) {
  const mins = Math.round(totalSeconds / 60)
  return mins < 1 ? '<1 min' : `${mins} min`
}

export default function History() {
  const [meetings, setMeetings] = useState(null) // null = loading, [] = loaded but empty (logged-in only)
  const [loadError, setLoadError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const { liveMeeting, setLiveMeeting, loadMeetingById, guestHistory, removeGuestMeeting } = useMeeting()
  const { isLoggedIn, token } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!isLoggedIn) return
    let cancelled = false

    function fetchMeetings(isInitial) {
      if (isInitial) {
        setMeetings(null)
        setLoadError(null)
      }
      authFetch(`${API_BASE}/meetings`, token)
        .then((res) => (res.ok ? res.json() : Promise.reject()))
        .then((data) => {
          if (!cancelled) setMeetings(data)
        })
        .catch(() => {
          // A background poll failing quietly (e.g. a dropped connection)
          // shouldn't blow away an already-loaded list with an error banner --
          // only the very first load surfaces that.
          if (!cancelled && isInitial) setLoadError('Could not load your meeting history, is the backend running?')
        })
    }

    fetchMeetings(true)

    // Cross-device sync: this page has no idea a recording just finished on
    // another device, so it polls while it's actually visible -- picks up a
    // meeting recorded on your laptop while you're looking at this on your
    // phone, without needing a manual refresh. Paused in the background so
    // it isn't burning requests/battery on a tab nobody's looking at.
    let interval = null
    function startPolling() {
      if (interval === null) interval = setInterval(() => fetchMeetings(false), 8000)
    }
    function stopPolling() {
      if (interval !== null) {
        clearInterval(interval)
        interval = null
      }
    }
    function onVisibilityChange() {
      if (document.hidden) stopPolling()
      else startPolling()
    }
    startPolling()
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      stopPolling()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [isLoggedIn, token])

  // Normalize both sources (backend summaries for logged-in users, full
  // guest results from localStorage) into the same shape for rendering.
  const rows = isLoggedIn
    ? (meetings ?? []).map((m) => ({
        id: m.id,
        title: m.title,
        date: m.date,
        speakerCount: m.speakerCount,
        durationSeconds: m.durationSeconds,
      }))
    : guestHistory.map((g) => ({
        id: g.meeting.id,
        title: g.meeting.title,
        date: g.meeting.date,
        speakerCount: g.meeting.speakerCount,
        durationSeconds: g.meeting.durationSeconds,
      }))

  const stillLoading = isLoggedIn && meetings === null && !loadError

  async function openMeeting(id) {
    if (!isLoggedIn) {
      const guestMeeting = guestHistory.find((g) => g.meeting.id === id)
      if (guestMeeting) {
        setLiveMeeting(guestMeeting)
        navigate('/transcript')
      }
      return
    }
    try {
      await loadMeetingById(id)
      navigate('/transcript')
    } catch (err) {
      console.error(err)
    }
  }

  async function deleteMeeting(id, title) {
    if (!window.confirm(`Delete "${title}"? This can't be undone.`)) return

    if (!isLoggedIn) {
      removeGuestMeeting(id)
      if (liveMeeting?.meeting?.id === id) setLiveMeeting(null)
      return
    }

    setDeletingId(id)
    try {
      const res = await authFetch(`${API_BASE}/meetings/${id}`, token, { method: 'DELETE' })
      if (!res.ok) throw new Error(`Server responded with ${res.status}`)

      setMeetings((prev) => prev.filter((m) => m.id !== id))
      if (liveMeeting?.meeting?.id === id) {
        setLiveMeeting(null) // don't leave a deleted meeting showing on Transcript/Notes/Reports
      }
    } catch (err) {
      setLoadError(`Could not delete that meeting (${err.message}).`)
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="page page-history">
      <p className="eyebrow">Your recordings</p>
      <h1>History</h1>
      <p className="hero-sub">Every meeting you've recorded, in one place, open one to revisit it, or delete it for good.</p>

      {!isLoggedIn && (
        <p className="guest-note">
          Viewing recordings saved on this browser only.{' '}
          <button type="button" className="link-inline" onClick={() => navigate('/login')}>
            Log in / sign up
          </button>{' '}
          to save your history to an account instead.
        </p>
      )}

      {isLoggedIn && loadError && <p className="status-banner status-error">{loadError}</p>}

      {stillLoading && <p className="hero-sub">Loading…</p>}

      {!stillLoading && rows.length === 0 && (
        <p className="hero-sub">No recordings yet, head to Record Meeting to make your first one.</p>
      )}

      {!stillLoading && rows.length > 0 && (
        <div className="history-list">
          {rows.map((m) => (
            <div className="history-row" key={m.id}>
              <button type="button" className="history-row-main" onClick={() => openMeeting(m.id)}>
                <p className="meeting-card-title">{m.title}</p>
                <p className="meeting-card-meta">
                  {m.date} · {m.speakerCount} speaker{m.speakerCount === 1 ? '' : 's'} · {formatDuration(m.durationSeconds)}
                </p>
              </button>
              <button
                type="button"
                className="btn btn-ghost history-delete-btn"
                onClick={() => deleteMeeting(m.id, m.title)}
                disabled={deletingId === m.id}
              >
                {deletingId === m.id ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
