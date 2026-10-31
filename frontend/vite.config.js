import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: true, // auto-opens the browser when the dev server starts, even if it lands on a different port
    // Vite rejects requests with an unrecognized Host header by default (DNS
    // rebinding protection) -- without this, accessing the dev server through
    // a Cloudflare quick tunnel (a *.trycloudflare.com hostname, not
    // localhost) gets blocked with "Blocked request. This host is not allowed."
    allowedHosts: ['.trycloudflare.com'],
  },
})