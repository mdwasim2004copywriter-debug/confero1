import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { useReducedMotion as useOSReducedMotion } from 'framer-motion'

const STORAGE_KEY = 'confero-force-animations'
const MotionPreferenceContext = createContext(null)

// App-wide (not just the logged-out cinematic page) so the opt-in works the
// same way whether someone is logged in or out, and on any route. It can't
// change the OS-level setting itself -- only that device's Settings app can
// do that -- but it makes Confero itself ignore that setting once a visitor
// explicitly opts back in here, and that opt-in behaves the same on any
// browser/device they use the app from, since it's app logic, not something
// borrowed from the OS.
export function MotionPreferenceProvider({ children }) {
  const osReducedMotion = useOSReducedMotion()
  const [forceAnimations, setForceAnimations] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      if (forceAnimations) localStorage.setItem(STORAGE_KEY, '1')
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      // localStorage unavailable (private mode etc.) -- preference just
      // won't persist across reloads, nothing else depends on it.
    }
    // Mirrors into a root attribute so plain CSS transitions/animations
    // (not driven by Framer Motion) can respect the same override -- see
    // the guarded `@media (prefers-reduced-motion: reduce)` block in
    // styles.css.
    document.documentElement.toggleAttribute('data-motion-override', forceAnimations)
  }, [forceAnimations])

  const toggleForceAnimations = useCallback(() => setForceAnimations((v) => !v), [])

  const reduceMotion = osReducedMotion && !forceAnimations

  return (
    <MotionPreferenceContext.Provider value={{ reduceMotion, osReducedMotion, forceAnimations, toggleForceAnimations }}>
      {children}
    </MotionPreferenceContext.Provider>
  )
}

export function useMotionPreference() {
  const ctx = useContext(MotionPreferenceContext)
  if (!ctx) throw new Error('useMotionPreference must be used within MotionPreferenceProvider')
  return ctx
}
