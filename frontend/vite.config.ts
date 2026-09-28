import { tanstackRouter } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [
    // autoCodeSplitting: each route's component loads with the route, so chart-heavy
    // pages (routes, cycle) no longer put recharts on the tracker's critical path.
    tanstackRouter({ target: 'react', routesDirectory: './src/routes', autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
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
    rolldownOptions: {
      output: {
        // Rolldown's native chunk groups, not the `manualChunks` shim. A group captures
        // its modules' dependencies recursively, so under the shim the deckgl group
        // (via deck.gl-leaflet) swallowed leaflet itself, and the eagerly-loaded map
        // downloaded all of deck.gl (~220 kB gz) to get it. Groups are assigned in
        // descending priority and an assigned module is never recaptured, so everything
        // the entry needs (react, router, leaflet) is claimed before the heavy groups.
        codeSplitting: {
          groups: [
            // Vite's __vitePreload helper wraps every dynamic import(). Left unclaimed, the
            // deckgl group captured it (deck.gl makes dynamic imports of its own), so every
            // page downloaded deck.gl to get a few hundred bytes of helper.
            { name: 'preload-helper', test: /vite[\\/]preload-helper/, priority: 200 },
            { name: 'react-dom', test: /[\\/]node_modules[\\/](react-dom|scheduler)[\\/]/, priority: 100 },
            { name: 'react', test: /[\\/]node_modules[\\/]react[\\/]/, priority: 95 },
            { name: 'tanstack-router', test: /[\\/]node_modules[\\/]@tanstack[\\/](react-router|router-core|history)[\\/]/, priority: 90 },
            { name: 'tanstack-query', test: /[\\/]node_modules[\\/]@tanstack[\\/](react-query|query-core)[\\/]/, priority: 90 },
            { name: 'lucide', test: /[\\/]node_modules[\\/]lucide-react[\\/]/, priority: 80 },
            // cn()'s deps. recharts also depends on clsx, so unclaimed it landed in the
            // recharts chunk and every page preloaded recharts for one small function.
            { name: 'ui-utils', test: /[\\/]node_modules[\\/](clsx|tailwind-merge)[\\/]/, priority: 80 },
            {
              name: 'leaflet',
              test: /[\\/]node_modules[\\/](leaflet|react-leaflet|@react-leaflet[\\/]core|leaflet\.markercluster)[\\/]/,
              priority: 70,
            },
            // maplibre-gl + its Leaflet binding: loaded by VectorBasemap's dynamic import.
            { name: 'maplibre', test: /[\\/]node_modules[\\/](maplibre-gl|@maplibre)[\\/]/, priority: 60 },
            // deck.gl and luma.gl stay in ONE chunk: splitting them broke luma's init order
            // (the `picking.defaultUniforms` crash). Loaded only in WebGL mode.
            {
              name: 'deckgl',
              test: /[\\/]node_modules[\\/](deck\.gl-leaflet|@deck\.gl|@luma\.gl|@loaders\.gl|@math\.gl|@probe\.gl|mjolnir\.js)[\\/]/,
              priority: 50,
            },
            // recharts + d3: analytics, dispersion, routes and cycle pages only.
            { name: 'recharts', test: /[\\/]node_modules[\\/](recharts|victory-vendor|d3-[^\\/]+|d3)[\\/]/, priority: 40 },
          ],
        },
      },
    },
  },
})
