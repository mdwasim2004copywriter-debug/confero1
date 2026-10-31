import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { API_BASE } from '../config/api.js'
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function passwordChecks(password) {
  return {
    length: password.length >= 8 && password.length <= 16,
    lower: /[a-z]/.test(password),
    upper: /[A-Z]/.test(password),
    number: /[0-9]/.test(password),
  }
}

function passwordStrength(password) {
  if (!password) return null
  const checks = passwordChecks(password)
  const metCount = Object.values(checks).filter(Boolean).length
  const hasSpecial = /[^A-Za-z0-9]/.test(password)

  if (metCount < 4) return 'weak'
  if (hasSpecial || password.length >= 13) return 'strong'
  return 'good'
}

export default function Login() {
  const [mode, setMode] = useState('login') // 'login' | 'signup'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [emailTouched, setEmailTouched] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const [captcha, setCaptcha] = useState(null) // { question, token }
  const [captchaAnswer, setCaptchaAnswer] = useState('')
  const [captchaLoading, setCaptchaLoading] = useState(false)

  const { login, signup } = useAuth()
  const navigate = useNavigate()

  const emailValid = EMAIL_PATTERN.test(email)
  const checks = passwordChecks(password)
  const strength = passwordStrength(password)

  async function loadCaptcha() {
    setCaptchaLoading(true)
    setCaptchaAnswer('')
    try {
      const res = await fetch(`${API_BASE}/auth/captcha`)
      const data = await res.json()
      setCaptcha(data)
    } catch {
      setCaptcha(null) // backend not reachable, form will just show the connection error on submit
    } finally {
      setCaptchaLoading(false)
    }
  }

  useEffect(() => {
    if (mode === 'signup' && !captcha) loadCaptcha()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (mode === 'signup') {
        if (!captcha) throw new Error('Could not load the verification question, check your connection and try again')
        await signup(email, password, {
          captchaAnswer: Number(captchaAnswer),
          captchaToken: captcha.token,
        })
      } else {
        await login(email, password)
      }
      navigate('/')
    } catch (err) {
      setError(err.message)
      if (mode === 'signup') loadCaptcha() // fresh question after any failed attempt
    } finally {
      setBusy(false)
    }
  }

  const signupDisabled =
    mode === 'signup' && (!emailValid || !Object.values(checks).every(Boolean) || !captchaAnswer || captchaLoading)

  return (
    <div className="page page-login">
      <div className="login-panel">
        <p className="eyebrow">{mode === 'signup' ? 'Create account' : 'Log in'}</p>
        <h1 className="login-title">{mode === 'signup' ? 'Get started' : 'Welcome back'}</h1>
        <p className="hero-sub">
          {mode === 'signup'
            ? 'An account keeps your own meetings separate and saved.'
            : 'Log in to see your saved meetings.'}
        </p>

        <form className="login-form" onSubmit={handleSubmit}>
          <label className="login-field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onBlur={() => setEmailTouched(true)}
              required
              autoComplete="email"
              placeholder="you@example.com"
            />
            {emailTouched && email && !emailValid && (
              <span className="field-error">Enter a valid email address (like name@example.com)</span>
            )}
          </label>

          <label className="login-field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
              placeholder={mode === 'signup' ? '8-16 characters, mixed case + number' : 'Your password'}
            />

            {mode === 'signup' && password && (
              <>
                <div className={`password-strength strength-${strength}`}>
                  <span className="strength-bar" />
                  <span className="strength-bar" />
                  <span className="strength-bar" />
                  <span className="strength-label">{strength}</span>
                </div>
                <ul className="password-checklist">
                  <li className={checks.length ? 'is-met' : ''}>8-16 characters</li>
                  <li className={checks.upper ? 'is-met' : ''}>An uppercase letter</li>
                  <li className={checks.lower ? 'is-met' : ''}>A lowercase letter</li>
                  <li className={checks.number ? 'is-met' : ''}>A number</li>
                </ul>
              </>
            )}
          </label>

          {mode === 'signup' && (
            <label className="login-field">
              <span>{captchaLoading ? 'Loading a quick check\u2026' : captcha?.question || 'Verification'}</span>
              <input
                type="text"
                inputMode="numeric"
                value={captchaAnswer}
                onChange={(e) => setCaptchaAnswer(e.target.value)}
                required
                placeholder="Your answer"
                disabled={captchaLoading || !captcha}
              />
              <span className="field-hint">Just confirming you're not a bot, no account needed for this part.</span>
            </label>
          )}

          {error && <p className="status-banner status-error">{error}</p>}

          <button type="submit" className="btn btn-primary" disabled={busy || signupDisabled}>
            {busy ? 'Please wait\u2026' : mode === 'signup' ? 'Create account' : 'Log in'}
          </button>
        </form>

        <button
          type="button"
          className="link-inline login-switch"
          onClick={() => {
            setMode(mode === 'signup' ? 'login' : 'signup')
            setError(null)
          }}
        >
          {mode === 'signup' ? 'Already have an account? Log in' : "Don't have an account? Sign up"}
        </button>
      </div>
    </div>
  )
}