import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the UI runs on :5173 and proxies /api (incl. WebSocket) to the local backend.
// `npm run build` writes the production UI into the backend package, which serves it itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8765', ws: true } },
  },
  build: { outDir: '../backend/musicorganizer/static', emptyOutDir: true },
})
