import { createContext, useContext, useState, useCallback } from 'react'
import { useAuth, authFetch } from './AuthContext.jsx'
import { API_BASE } from '../config/api.js'

// Holds the result of a real recording (from the backend API), if any.
// Pages check this first and fall back to the sample data in
// data/mockMeeting.js when it's null, that's what lets a real recording
// and the demo/sample view coexist without breaking either one.
//
// Persisted to localStorage so refreshing the page (or closing and
// reopening the tab) doesn't lose whatever meeting you were just looking
// at. The full history of every meeting lives on the backend (SQLite,
// see backend/storage.py), localStorage here just remembers "which one
// was I viewing," it's not the source of truth.
const MeetingContext = createContext(null)

const STORAGE_KEY = 'confero:lastViewedMeeting'
// Guests have no account to save recordings to (the backend requires a
// real user_id, see backend/storage.py), so their recordings never touch
// the server. This is the guest-only substitute: a capped list of full
// meeting results kept in this browser's localStorage, so History has
// something to show them without requiring login. It's local to this
// browser/device only, clearing site data loses it, same trade-off as
// any guest-mode data.
const GUEST_HISTORY_KEY = 'confero:guestHistory'
const GUEST_HISTORY_LIMIT = 15

function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function loadGuestHistory() {
  try {
    const raw = localStorage.getItem(GUEST_HISTORY_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export function MeetingProvider({ children }) {
  const [liveMeeting, setLiveMeetingState] = useState(loadFromStorage)
  const [guestHistory, setGuestHistoryState] = useState(loadGuestHistory)
  const { token } = useAuth() // MeetingProvider is nested inside AuthProvider in main.jsx

  const setLiveMeeting = useCallback((meeting) => {
    setLiveMeetingState(meeting)
    try {
      if (meeting) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(meeting))
      } else {
        localStorage.removeItem(STORAGE_KEY)
      }
    } catch {
      // localStorage can fail (private browsing, quota, etc), the app
      // still works, it just won't survive a refresh in that case.
    }
  }, [])

  // Adds one freshly-processed guest recording to the front of the local
  // history list, evicting the oldest once past GUEST_HISTORY_LIMIT so
  // localStorage doesn't grow without bound.
  const addGuestMeeting = useCallback(
    (meeting) => {
      setGuestHistoryState((prev) => {
        const withoutDuplicate = prev.filter((m) => m.meeting.id !== meeting.meeting.id)
        const next = [meeting, ...withoutDuplicate].slice(0, GUEST_HISTORY_LIMIT)
        try {
          localStorage.setItem(GUEST_HISTORY_KEY, JSON.stringify(next))
        } catch {
          // non-fatal if storage is unavailable (private browsing, quota, etc)
        }
        return next
      })
    },
    []
  )

  const removeGuestMeeting = useCallback(
    (id) => {
      setGuestHistoryState((prev) => {
        const next = prev.filter((m) => m.meeting.id !== id)
        try {
          localStorage.setItem(GUEST_HISTORY_KEY, JSON.stringify(next))
        } catch {
          // non-fatal, see above
        }
        return next
      })
    },
    []
  )

  // Keeps a guest's local History list in sync after a speaker rename on
  // the Transcript page, same storage key/shape as the other guest-history
  // writers above, just updating one entry's speakers instead of adding or
  // removing a whole meeting.
  const updateGuestMeetingSpeakers = useCallback(
    (id, speakers) => {
      setGuestHistoryState((prev) => {
        const next = prev.map((m) => (m.meeting.id === id ? { ...m, speakers } : m))
        try {
          localStorage.setItem(GUEST_HISTORY_KEY, JSON.stringify(next))
        } catch {
          // non-fatal, see above
        }
        return next
      })
    },
    []
  )

  // Fetches one saved meeting's full detail from the backend and makes it
  // the current view, used when clicking a past meeting on the Home page.
  // Requires being logged in, since meetings are now per-user.
  const loadMeetingById = useCallback(
    async (id) => {
      if (!token) throw new Error('Log in to view your saved meetings.')
      const res = await authFetch(`${API_BASE}/meetings/${id}`, token)
      if (!res.ok) throw new Error(`Could not load meeting ${id} (${res.status})`)
      const data = await res.json()
      setLiveMeeting(data)
      return data
    },
    [setLiveMeeting, token]
  )

  return (
    <MeetingContext.Provider
      value={{
        liveMeeting,
        setLiveMeeting,
        loadMeetingById,
        guestHistory,
        addGuestMeeting,
        removeGuestMeeting,
        updateGuestMeetingSpeakers,
      }}
    >
      {children}
    </MeetingContext.Provider>
  )
}

export function useMeeting() {
  const ctx = useContext(MeetingContext)
  if (!ctx) {
    throw new Error('useMeeting must be used inside <MeetingProvider>')
  }
  return ctx
}