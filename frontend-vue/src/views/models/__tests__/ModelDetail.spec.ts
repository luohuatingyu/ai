import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { mountPage } from '@/test/mountPage'
import ModelDetail from '../../models/ModelDetail.vue'

const { apiGet, apiPost, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const MODEL = {
  id: 'm-1',
  name: 'iris-rf',
  version: 'v1',
  task: 'classification',
  framework: 'sklearn',
  status: 'ready',
  description: '鸢尾花随机森林',
  source_job_id: 'j-9',
  deployed_endpoint: '/deploy/dep-1',
  created_at: 1700000000,
  updated_at: 1700001000,
  metrics: { accuracy: 0.9555, f1: 0.9412 },
  artifacts: { best_pt: '/data/models/m-1/best.pt', meta: '/data/models/m-1/meta.json' },
  deployments: [
    { id: 'dep-1', endpoint_name: 'iris-api', status: 'running', created_at: 1700002000 },
  ],
}

const routes = [{ path: '/models/detail/:id', component: ModelDetail }]

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiDelete.mockReset()
})

describe('ModelDetail', () => {
  it('渲染概览：版本、任务类型、来源任务链接、端点', async () => {
    apiGet.mockResolvedValue(MODEL)
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })
    const text = wrapper.text()
    expect(text).toContain('iris-rf')
    expect(text).toContain('可用')
    expect(text).toContain('分类')
    expect(text).toContain('sklearn')
    expect(text).toContain('j-9') // 来源任务
    expect(text).toContain('/deploy/dep-1')
    expect(text).toContain('鸢尾花随机森林')
  })

  it('渲染评估指标与产物路径', async () => {
    apiGet.mockResolvedValue(MODEL)
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })
    const text = wrapper.text()
    expect(text).toContain('accuracy')
    expect(text).toContain('0.9555')
    expect(text).toContain('f1')
    expect(text).toContain('/data/models/m-1/best.pt')
    // 部署记录
    expect(text).toContain('dep-1')
    expect(text).toContain('iris-api')
  })

  it('部署对话框提交创建部署并刷新详情', async () => {
    apiGet.mockResolvedValue(MODEL)
    apiPost.mockResolvedValue({ deployment_id: 'dep-new' })
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })

    const deployBtn = wrapper.findAll('button').find((b: any) => b.text().includes('部署服务'))
    await deployBtn!.trigger('click')
    await flushPromises()

    // 填写端点名称并提交
    const endpointInput = wrapper.find('input[placeholder*="sales-api"]')
    await endpointInput.setValue('sales-api')
    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建部署'))
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith('/models/m-1/deploy', {
      model_version: 'v1',
      endpoint_name: 'sales-api',
      replicas: 1,
      config: {},
    })
    expect(apiGet).toHaveBeenCalledWith('/models/m-1') // 创建后刷新
  })

  it('归档带确认并调用 DELETE', async () => {
    apiGet.mockResolvedValue(MODEL)
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })

    const archiveBtn = wrapper.findAll('button').find((b: any) => b.text().includes('归档'))
    await archiveBtn!.trigger('click')
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('iris-rf')
    expect(apiDelete).toHaveBeenCalledWith('/models/m-1')
  })

  it('已归档模型隐藏部署与归档操作按钮', async () => {
    apiGet.mockResolvedValue({ ...MODEL, status: 'archived', deployments: [] })
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })
    expect(wrapper.text()).toContain('已归档')
    // 操作按钮不渲染（按钮级断言，避免与文案混淆）
    expect(wrapper.findAll('button').find((b: any) => b.text().includes('部署服务'))).toBeUndefined()
    expect(wrapper.findAll('button').find((b: any) => b.text() === '归档')).toBeUndefined()
  })

  it('加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('模型不存在'))
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })
    expect(wrapper.text()).toContain('模型加载失败')

    apiGet.mockResolvedValue(MODEL)
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('iris-rf')
  })

  it('模型 Hero 前置展示版本、关键指标与部署动作', async () => {
    apiGet.mockResolvedValue(MODEL)
    const { wrapper } = await mountPage(ModelDetail, { routes, path: '/models/detail/m-1' })
    const hero = wrapper.find('.model-hero')
    expect(hero.exists()).toBe(true)
    expect(hero.text()).toContain('v1') // 版本前置
    expect(hero.text()).toContain('0.9555') // 指标 chips
    // Hero 内提供部署与下载动作
    expect(hero.findAll('button').some((b) => b.text().includes('部署服务'))).toBe(true)
    expect(hero.findAll('button').some((b) => b.text().includes('下载产物'))).toBe(true)
  })
})
