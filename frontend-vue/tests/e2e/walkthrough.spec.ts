import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * 全量页面走查（A1 验收）：24 个业务路由 × 4 个视口。
 * - 校验应用外壳（顶栏/侧栏/页面容器）在每个页面渲染；
 * - 校验无未捕获的页面级 JS 错误（接口 404/网络类错误允许，属数据缺失场景）；
 * - 每页每视口截图存入 OUT_DIR 供人工视觉走查。
 * 运行方式：E2E_BASE_URL=http://localhost:8000 npx playwright test tests/e2e/walkthrough.spec.ts
 */

const OUT_DIR = process.env.WALKTHROUGH_DIR || join(process.cwd(), 'walkthrough-shots')

const VIEWPORTS = [
  { name: '1440', width: 1440, height: 900 },
  { name: '1280', width: 1280, height: 800 },
  { name: '768', width: 768, height: 1024 },
  { name: '390', width: 390, height: 844 },
]

// 24 个业务路由（与 router/index.ts 一致）
const ROUTES: Array<{ path: string; title: string }> = [
  { path: '/dashboard', title: '仪表盘' },
  { path: '/datasets/list', title: '数据集列表' },
  { path: '/datasets/import', title: '导入数据集' },
  { path: '/datasets/huggingface', title: 'HuggingFace 数据集' },
  { path: '/workbench/sessions', title: '会话管理' },
  { path: '/workbench/new', title: '新建会话' },
  { path: '/workbench/session/walkthrough-fake', title: '会话详情' },
  { path: '/templates/library', title: '模板库' },
  { path: '/templates/my', title: '我的模板' },
  { path: '/templates/create', title: '创建模板' },
  { path: '/templates/detail/walkthrough-fake', title: '模板详情' },
  { path: '/training/jobs', title: '任务列表' },
  { path: '/training/create', title: '创建训练任务' },
  { path: '/training/detail/walkthrough-fake', title: '任务详情' },
  { path: '/models/registry', title: '模型注册表' },
  { path: '/models/deployments', title: '部署管理' },
  { path: '/models/detail/walkthrough-fake', title: '模型详情' },
  { path: '/pipelines/list', title: 'Pipeline 列表' },
  { path: '/pipelines/create', title: '创建 Pipeline' },
  { path: '/pipelines/runs', title: '执行历史' },
  { path: '/pipelines/detail/walkthrough-fake', title: 'Pipeline 详情' },
  { path: '/pipelines/run/walkthrough-fake/walkthrough-fake', title: '执行详情' },
  { path: '/clusters/workers', title: 'Worker 管理' },
  { path: '/clusters/remote', title: '远程服务器' },
]

const results: Array<{ route: string; viewport: string; shellOk: boolean; pageErrors: string[] }> = []

for (const viewport of VIEWPORTS) {
  test.describe(`走查 @${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } })

    for (const route of ROUTES) {
      test(`${route.title} [${route.path}]`, async ({ page }, testInfo) => {
        const pageErrors: string[] = []
        page.on('pageerror', (err) => pageErrors.push(String(err)))

        await page.goto(route.path, { waitUntil: 'networkidle', timeout: 20000 }).catch(() => {
          // networkidle 可能因轮询接口永不静默，降级为 load
          return page.goto(route.path, { waitUntil: 'load', timeout: 20000 })
        })
        await page.waitForTimeout(600)

        // 应用外壳必须渲染（窄屏侧栏隐藏，检查顶栏与主区）
        await expect(page.locator('.app-header__name')).toContainText('MTP 模型训练平台')
        await expect(page.locator('.app-main .page-view').first()).toBeVisible()

        // 页面标题正确（面包屑/页面头）
        const pageTitle = page.locator('.page-title').first()
        if (route.path.includes('walkthrough-fake')) {
          // 不存在的详情页：标题为默认"加载中"或错误态，仅要求外壳可用
          expect(pageTitle).toBeVisible()
        } else {
          await expect(pageTitle).toHaveText(route.title, { timeout: 5000 })
        }

        // 截图存档
        const dir = join(OUT_DIR, viewport.name)
        mkdirSync(dir, { recursive: true })
        const shot = join(dir, `${route.path.replace(/\//g, '_') || 'root'}.png`)
        await page.screenshot({ path: shot, fullPage: false })

        // 未捕获 JS 错误允许网络类（接口不可用），但不得有渲染类异常
        const rendering = pageErrors.filter((e) => !/Failed to fetch|NetworkError|404|500|502|503|ECONNREFUSED|timeout|aborted/i.test(e))
        results.push({ route: route.path, viewport: viewport.name, shellOk: true, pageErrors: rendering })
        if (rendering.length > 0) {
          console.error(`[walkthrough] ${route.path}@${viewport.name} 页面错误:`, rendering)
        }
        expect(rendering).toEqual([])
      })
    }
  })
}

test.afterAll(async () => {
  // 汇总清单落盘（JSON 供 TEST_REPORT 引用）
  mkdirSync(OUT_DIR, { recursive: true })
  writeFileSync(join(OUT_DIR, 'summary.json'), JSON.stringify(results, null, 2))
  const failed = results.filter((r) => r.pageErrors.length > 0)
  console.log(`[walkthrough] 共 ${results.length} 项检查，其中 ${failed.length} 项含渲染类错误`)
})
