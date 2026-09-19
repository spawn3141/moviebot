import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The build lands inside the Python package, so `moviebot serve` can ship it.
// During development (`npm run dev`), API calls are proxied to the Python server.
export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../moviebot/web', emptyOutDir: true },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8080' } },
})
