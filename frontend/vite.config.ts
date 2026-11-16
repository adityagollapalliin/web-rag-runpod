import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const backend = process.env.DOCQA_BACKEND_URL ?? 'http://127.0.0.1:8000'

// In development the browser talks to Vite, which forwards /api to FastAPI, so cookies are
// same-origin exactly as they are in production (where FastAPI serves the built files).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': backend } },
  test: { environment: 'node' },
})
