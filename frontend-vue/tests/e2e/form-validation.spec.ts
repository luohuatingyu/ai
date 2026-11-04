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

test('登记远程服务器：必填项为空时校验拦截，不发起登记', async ({ page }) => {
  await page.goto('/clusters/remote')
  await page.locator('button', { hasText: '登记服务器' }).click()
  // 对话框内直接点击登记（未填写任何字段）
  await page.locator('.el-dialog button', { hasText: '登记' }).last().click()
  await expect(page.locator('.el-form-item__error').first()).toContainText('请输入机器 ID')
  await page.waitForTimeout(300)
  // 对话框仍打开（未提交关闭）
  await expect(page.locator('.el-dialog')).toBeVisible()
})
