import { test, expect } from '@playwright/test'

/**
 * 表单校验回归（真实浏览器）：
 * Element Plus 表单校验在 happy-dom 单元测试环境下不生效（放行空必填值），
 * 因此"校验拦截提交"这一验收点统一在本文件用真实浏览器覆盖。
 */

test('HuggingFace 导入：仓库 ID 为空时校验拦截，不发起请求', async ({ page }) => {
  await page.goto('/datasets/huggingface')
  await page.locator('button', { hasText: '导入数据集' }).click()
  await expect(page.locator('.el-form-item__error').first()).toContainText('请输入 HuggingFace 仓库 ID')
  // 停留在当前页，未跳转列表
  await page.waitForTimeout(300)
  expect(page.url()).toContain('/datasets/huggingface')
})

test('新建会话：名称为空时校验拦截，不发起创建', async ({ page }) => {
  await page.goto('/workbench/new')
  await page.locator('button', { hasText: '创建并打开' }).click()
  await expect(page.locator('.el-form-item__error').first()).toContainText('请输入会话名称')
  await page.waitForTimeout(300)
  expect(page.url()).toContain('/workbench/new')
})
