import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { realpathSync } from 'node:fs'

const frontendRoot = realpathSync(process.cwd())

export default defineConfig({
  root: frontendRoot,
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
