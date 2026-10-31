// Single source of truth for the backend URL. Defaults to localhost:8000
// (unchanged local dev behavior) unless VITE_API_BASE_URL is set at build
// time, e.g. to the hosted backend's URL when deployed on Vercel.
const API_ROOT = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export const API_BASE = `${API_ROOT}/api`
