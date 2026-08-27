import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: '../demo_api/static', emptyOutDir: true },
  server: {
    proxy: {
      // ws:true is required or the /api/v1/ws/cdot-live socket never upgrades
      // under `npm run dev` and the console silently stops updating.
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
      '/metrics': 'http://127.0.0.1:8000',
    },
  },
})

