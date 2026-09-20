import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物由 FastAPI（`tp serve`）在 `/` 下托管，接口在 `/api`。
// 开发时用 `npm run dev`，把 /api 代理到本地服务，免得前端跨域。
// 代理目标可用 `TRANSBOOK_API` 覆盖（`web/.env.local` 或环境变量）。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'TRANSBOOK')
  const target = env.TRANSBOOK_API || 'http://127.0.0.1:8321'

  return {
    plugins: [react()],
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      assetsDir: 'assets',
    },
    server: {
      port: 5173,
      proxy: {
        '/api': { target, changeOrigin: true },
      },
    },
  }
})
