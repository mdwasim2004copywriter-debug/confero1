import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Temporary config used only for one-off mobile testing via a Cloudflare
// tunnel (see the .env.mobile-test file next to this one). Not used by the
// normal `npm run dev` workflow, which keeps using vite.config.js as-is.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    host: true, // bind to 0.0.0.0 so the tunnel can reach it
    allowedHosts: ['.trycloudflare.com'],
  },
})
