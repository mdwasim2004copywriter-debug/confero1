import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext.jsx'
import { useMotionPreference } from '../context/MotionPreferenceContext.jsx'

const links = [
  { to: '/', label: 'Home', end: true },
  { to: '/record', label: 'Record Meeting' },
  { to: '/transcript', label: 'Transcript' },
  { to: '/notes', label: 'Notes' },
  { to: '/reports', label: 'Reports' },
  { to: '/history', label: 'History' },
  { to: '/dashboard', label: 'Dashboard' },
]

// Only shows up when the browser is actually reporting reduced-motion --
// no point offering the toggle to visitors it doesn't apply to. Lives here
// (not inside the cinematic page) so it's available on every route, logged
// in or out.
function MotionToggle() {
  const { osReducedMotion, forceAnimations, toggleForceAnimations } = useMotionPreference()
  if (!osReducedMotion) return null
  return (
    <button
      type="button"
      className={`motion-toggle${forceAnimations ? ' is-on' : ''}`}
      onClick={toggleForceAnimations}
      title="Your system is set to reduce motion. Turn this on to see Confero's animations anyway."
    >
      {forceAnimations ? 'Animations: on' : 'Enable animations'}
    </button>
  )
}

export default function Navbar() {
  const { isLoggedIn, email, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  function handleLogout() {
    logout()
    navigate('/')
  }

  // The cinematic marketing pitch (logged-out Home) gets a dark,
  // transparent header instead of the bright pill nav, so it blends with
  // the sections below -- but it's a single row of real links next to
  // Log in, same as every other page. No second nav mechanism, no
  // hidden-behind-a-toggle menu.
  const isCinematicPitch = !isLoggedIn && location.pathname === '/'

  if (isCinematicPitch) {
    return (
      <header className="navbar navbar-minimal">
        <div className="navbar-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">Confero</span>
          </div>
          <nav className="navbar-minimal-links">
            {links.slice(1).map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) => 'navbar-minimal-link' + (isActive ? ' active' : '')}
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <div className="navbar-auth">
            <MotionToggle />
            <NavLink to="/login" className="btn btn-primary navbar-login-btn">
              Log in
            </NavLink>
          </div>
        </div>
      </header>
    )
  }

  return (
    <header className="navbar">
      <div className="navbar-inner">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">Confero</span>
        </div>
        <nav className="nav-links">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="navbar-auth">
          <MotionToggle />
          {isLoggedIn ? (
            <>
              <span className="navbar-auth-email">{email}</span>
              <button type="button" className="link-inline" onClick={handleLogout}>
                Log out
              </button>
            </>
          ) : (
            <NavLink to="/login" className="btn btn-ghost navbar-login-btn">
              Log in
            </NavLink>
          )}
        </div>
      </div>
    </header>
  )
}