import tailwind from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const target = process.env.CANDLEFLOW_API_PROXY ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwind()],
  server: {
    proxy: Object.fromEntries(['/api', '/docs', '/openapi.json'].map(path => [path, { target }])),
  },
})
