import { createContext, useContext, useState, useCallback } from 'react'
import { API_BASE } from '../config/api.js'

// Holds the logged-in user's session token. Persisted to localStorage so
// refreshing the page (or closing and reopening the tab) doesn't log you
// out. The token itself is a signed JWT the backend issues on signup/
// login, this context just stores it and attaches it to API calls that
// need it (see authFetch below).
const AuthContext = createContext(null)

const TOKEN_KEY = 'confero:authToken'
const EMAIL_KEY = 'confero:authEmail'

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => {
    try {
      return localStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  })
  const [email, setEmailState] = useState(() => {
    try {
      return localStorage.getItem(EMAIL_KEY)
    } catch {
      return null
    }
  })

  const setSession = useCallback((newToken, newEmail) => {
    setTokenState(newToken)
    setEmailState(newEmail)
    try {
      if (newToken) {
        localStorage.setItem(TOKEN_KEY, newToken)
        localStorage.setItem(EMAIL_KEY, newEmail)
      } else {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(EMAIL_KEY)
      }
    } catch {
      // localStorage can fail (private browsing, quota), session still
      // works for this tab, it just won't survive a refresh.
    }
  }, [])

  const signup = useCallback(
    async (emailInput, password, captcha) => {
      const res = await fetch(`${API_BASE}/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: emailInput,
          password,
          captcha_answer: captcha?.captchaAnswer,
          captcha_token: captcha?.captchaToken,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Could not create account')
      setSession(data.token, data.email)
    },
    [setSession]
  )

  const login = useCallback(
    async (emailInput, password) => {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: emailInput, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Could not log in')
      setSession(data.token, data.email)
    },
    [setSession]
  )

  const logout = useCallback(() => setSession(null, null), [setSession])

  return (
    <AuthContext.Provider value={{ token, email, isLoggedIn: Boolean(token), signup, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return ctx
}

/** fetch() wrapper that attaches the logged-in user's token automatically,
 * use this instead of plain fetch() for any call to a protected
 * endpoint (meetings list/detail, process-meeting). Omits the header
 * entirely when there's no token, so the backend sees a guest request
 * rather than a literal "Bearer null". */
export async function authFetch(url, token, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (token) headers.Authorization = `Bearer ${token}`
  return fetch(url, { ...options, headers })
}