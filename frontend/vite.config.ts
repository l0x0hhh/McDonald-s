import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Keep the frontend Host: LAN HTTP may omit Fetch Metadata, so the backend
// must still be able to compare the submitted Origin to its original authority.
const apiProxy = { '/api': { target: 'http://127.0.0.1:18000', changeOrigin: false } }

export default defineConfig({
  plugins: [react()],
  // Verification copies contain HTML/tsconfig files, but must never reset a live meal form.
  server: { port: 5173, strictPort: true, proxy: apiProxy, watch: { ignored: ['**/verification/**'] } },
  preview: { proxy: apiProxy },
  test: {
    environment: 'jsdom',
    maxWorkers: 2,
    testTimeout: 15000,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.{test,spec}.{ts,tsx}'],
    css: false,
  },
})
