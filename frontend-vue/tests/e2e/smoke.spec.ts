import { test, expect } from '@playwright/test'

/**
 * 应用外壳冒烟用例：不依赖后端数据，验证框架层可渲染。
 * 完整业务链路用例（依赖真实后端）在阶段 6 全量验收时补充。
 */
test('应用外壳渲染：品牌、侧栏导航与仪表盘标题', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(page.locator('.app-header__name')).toContainText('MTP 模型训练平台')
  await expect(page.locator('.app-aside .app-nav')).toBeVisible()
  await expect(page.locator('.page-title')).toHaveText('仪表盘')
})

test('侧栏导航可跳转到数据集列表', async ({ page }) => {
  await page.goto('/dashboard')
  await page.locator('.app-nav >> text=数据集管理').click()
  await page.locator('.app-nav >> text=数据集列表').click()
  await expect(page.locator('.page-title')).toHaveText('数据集列表')
})
