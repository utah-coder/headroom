import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Local only: `python -m analyst.local` serves the what-if API here. `vite preview`
  // uses the same proxy. In production CloudFront routes /api/* to the Lambda.
  server: {
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
})
