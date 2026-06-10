import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [TanStackRouterVite({ routesDirectory: './src/routes' }), react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8003',
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('@tanstack/react-router')) return 'tanstack-router'
          if (id.includes('@tanstack/react-query')) return 'tanstack-query'
          if (id.includes('leaflet')) return 'leaflet'
          if (id.includes('lucide-react')) return 'lucide'
          if (id.includes('react-dom')) return 'react-dom'
          if (id.match(/[\\/]react[\\/]/)) return 'react'
        },
      },
    },
  },
})
