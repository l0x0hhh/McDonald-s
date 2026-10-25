import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Verification copies contain HTML/tsconfig files, but must never reset a live meal form.
  server: { port: 5173, strictPort: true, watch: { ignored: ['**/verification/**'] } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.{test,spec}.{ts,tsx}'],
    css: false,
  },
})
