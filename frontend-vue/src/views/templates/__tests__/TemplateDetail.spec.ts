import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import TemplateDetail from '../../templates/TemplateDetail.vue'

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

const DETAIL = {
  id: 't-1',
  name: 'XGBoost 分类',
  category: '模型训练',
  task_type: 'classification',
  description: '梯度提升分类模板',
  is_builtin: true,
  created_at: 1700000000,
  updated_at: 1700001000,
  parameters: [
    { name: 'epochs', type: 'number', label: '训练轮数', required: true, default: 20, description: '' },
  ],
  content: 'train({{epochs}})',
}

const routes = [{ path: '/templates/detail/:id', component: TemplateDetail }]

beforeEach(() => {
  apiGet.mockReset()
})

describe('TemplateDetail', () => {
  it('渲染模板概览：名称、来源标记、任务类型与时间', async () => {
    apiGet.mockResolvedValue(DETAIL)
    const { wrapper } = await mountPage(TemplateDetail, { routes, path: '/templates/detail/t-1' })
    const text = wrapper.text()
    expect(text).toContain('XGBoost 分类')
    expect(text).toContain('内置')
    expect(text).toContain('模型训练')
    expect(text).toContain('分类')
    expect(text).toContain('梯度提升分类模板')
  })

  it('渲染参数定义表与脚本内容', async () => {
    apiGet.mockResolvedValue(DETAIL)
    const { wrapper } = await mountPage(TemplateDetail, { routes, path: '/templates/detail/t-1' })
    expect(wrapper.text()).toContain('训练轮数')
    expect(wrapper.text()).toContain('epochs')
    expect(wrapper.text()).toContain('20') // 默认值
    expect(wrapper.find('.code-content').text()).toContain('train({{epochs}})')
  })

  it('内置模板隐藏编辑按钮，使用模板可跳转', async () => {
    apiGet.mockResolvedValue(DETAIL)
    const { wrapper, router } = await mountPage(TemplateDetail, { routes, path: '/templates/detail/t-1' })
    expect(wrapper.text()).not.toContain('编辑')
    const useBtn = wrapper.findAll('button').find((b: any) => b.text().includes('使用模板'))
    await useBtn!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/training/create')
    expect(router.currentRoute.value.query.template).toBe('t-1')
  })

  it('用户模板显示编辑按钮且携带 ID', async () => {
    apiGet.mockResolvedValue({ ...DETAIL, is_builtin: false })
    const { wrapper, router } = await mountPage(TemplateDetail, { routes, path: '/templates/detail/t-1' })
    const editBtn = wrapper.findAll('button').find((b: any) => b.text().includes('编辑'))
    await editBtn!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.id).toBe('t-1')
  })

  it('加载失败时展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('模板不存在'))
    const { wrapper } = await mountPage(TemplateDetail, { routes, path: '/templates/detail/t-1' })
    expect(wrapper.text()).toContain('模板加载失败')
    expect(wrapper.text()).toContain('模板不存在')

    apiGet.mockResolvedValue(DETAIL)
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('XGBoost 分类')
  })
})
