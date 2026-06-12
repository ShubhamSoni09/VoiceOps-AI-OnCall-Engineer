import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5191,
    strictPort: true,
    proxy: {
      '/auth': 'http://127.0.0.1:8001',
      '/console': 'http://127.0.0.1:8001',
      '/voice': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
    },
  },
})
