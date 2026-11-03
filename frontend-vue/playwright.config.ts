import { defineConfig } from '@playwright/test'

/**
 * 浏览器端到端测试配置。
 * - 默认由 Playwright 拉起 `npm run dev`（Vite 开发服务器，3000 端口）；
 * - 已有运行中的服务时通过 E2E_BASE_URL 指定并复用（如 docker 部署入口）；
 * - 后端 API 依赖 /api 代理：完整链路用例要求本地 8000 端口启动 trainer 服务。
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: 'npm run dev',
        url: 'http://localhost:3000',
        reuseExistingServer: true,
        timeout: 60000,
      },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})
