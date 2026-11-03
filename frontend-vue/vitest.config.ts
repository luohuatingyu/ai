import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'path'

/**
 * 组件测试专用配置：
 * - 环境使用 happy-dom，模拟浏览器 DOM 能力（无需真实浏览器）；
 * - 与构建配置保持相同的 '@' 别名，测试文件可直接引用源码模块；
 * - 覆盖率统计范围限定在业务源码（src），排除类型声明与测试文件本身。
 */
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['node_modules/**', 'dist/**', 'tests/e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**'],
      exclude: ['src/**/*.d.ts', 'src/auto-imports.d.ts', 'src/components.d.ts', 'src/**/__tests__/**'],
    },
  },
})
