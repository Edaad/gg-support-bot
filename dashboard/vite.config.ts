import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      // gg-computer (default port 3000); set VITE_WEEKLY_STATS_BASE_URL to override in dev
      '/weekly-stats': {
        target: 'http://127.0.0.1:3000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/weekly-stats/, ''),
      },
    },
  },
})
