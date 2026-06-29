import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const devPort = Number(process.env.VITE_DEV_PORT || 5191)
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8001'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('/react/') || id.includes('/react-dom/') || id.includes('/scheduler/')) {
            return 'vendor-react'
          }
          if (id.includes('/@phosphor-icons/')) return 'vendor-icons'
          if (id.includes('/@openuidev/')) return 'vendor-openui'
          if (id.includes('/react-syntax-highlighter/') || id.includes('/refractor/') || id.includes('/highlight')) {
            return 'vendor-code'
          }
          return 'vendor'
        },
      },
    },
  },
  server: {
    port: devPort,
    strictPort: true,
    proxy: {
      '/auth': apiTarget,
      '/agents': apiTarget,
      '/collab': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
      '/console': apiTarget,
      '/external-agents': apiTarget,
      '/system': apiTarget,
      '/speakers': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
      '/voice': apiTarget,
      '/workspace': apiTarget,
      '/health': apiTarget,
    },
  },
})
