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
      // Force deck.gl-leaflet's ESM build. Its package.json `browser` field points at a
      // self-contained UMD bundle (deck.gl + luma.gl baked in); Vite prefers `browser`, so
      // without this it pre-bundles a SECOND copy of luma.gl ("This version of luma.gl has
      // already been initialized" + the picking.defaultUniforms crash). The ESM build imports
      // @deck.gl/core as an external, so it shares the single pre-bundled copy below.
      'deck.gl-leaflet': path.resolve(
        __dirname,
        'node_modules/deck.gl-leaflet/dist/deck.gl-leaflet.esm.js'
      ),
    },
    dedupe: ['@luma.gl/shadertools', '@luma.gl/core', '@luma.gl/engine', '@luma.gl/webgl', '@deck.gl/core'],
  },
  optimizeDeps: {
    // Pre-bundle deck.gl-leaflet together with every deck.gl/luma.gl scoped package as one
    // entry set. This keeps a single shared @luma.gl/* (one luma.gl singleton) and inlines
    // @luma.gl/shadertools with the deck.gl shaderlib so it initializes before deck.gl reads
    // `picking.defaultUniforms` (the "Cannot read properties of undefined (reading
    // 'defaultUniforms')" crash came from that init order being broken across chunks).
    include: [
      'deck.gl-leaflet',
      '@deck.gl/core',
      '@deck.gl/layers',
      '@deck.gl/aggregation-layers',
      '@luma.gl/core',
      '@luma.gl/engine',
      '@luma.gl/shadertools',
      '@luma.gl/webgl',
    ],
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
          if (id.includes('@luma.gl')) return 'luma'
          if (id.includes('@deck.gl') || id.includes('deck.gl')) return 'deckgl'
          if (id.includes('leaflet')) return 'leaflet'
          if (id.includes('lucide-react')) return 'lucide'
          // recharts + its d3 deps: deferred to analytics/dispersion pages
          if (id.includes('recharts') || id.includes('/d3-') || id.includes('/d3/')) return 'recharts'
          if (id.includes('react-dom')) return 'react-dom'
          if (id.match(/[\\/]react[\\/]/)) return 'react'
        },
      },
    },
  },
})
