import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import TemplateLibrary from '../../templates/TemplateLibrary.vue'

const { apiGet, apiDelete } = vi.hoisted(() => ({ apiGet: vi.fn(), apiDelete: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const TEMPLATES = [
  { id: 't-1', name: 'XGBoost 分类', category: '模型训练', task_type: 'classification', description: '梯度提升分类模板', is_builtin: true, parameters: [{ name: 'epochs', label: '训练轮数' }, { name: 'lr', label: '学习率' }] },
  { id: 't-2', name: '销量预测', category: '模型训练', task_type: 'regression', description: '', is_builtin: false, parameters: [] },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/templates') return Promise.resolve({ templates: TEMPLATES, total: 2 })
    if (url === '/templates/categories') return Promise.resolve({ categories: [{ name: '模型训练' }] })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiDelete.mockReset()
})

describe('TemplateLibrary', () => {
  it('渲染模板卡片：分类、内置标记、任务类型与参数预览', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(TemplateLibrary)
    const text = wrapper.text()
    expect(text).toContain('XGBoost 分类')
    expect(text).toContain('内置')
    expect(text).toContain('分类') // 任务类型标签
    expect(text).toContain('训练轮数')
    expect(text).toContain('共 2 个模板')
  })

  it('筛选变化时携带分类/来源/搜索参数并回到第一页', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(TemplateLibrary)
    apiGet.mockClear()

    // 分类选择（第一个 el-select）：update:modelValue + change（模拟用户选择）
    const categorySelect = wrapper.findComponent({ name: 'ElSelect' })
    categorySelect.vm.$emit('update:modelValue', '模型训练')
    categorySelect.vm.$emit('change', '模型训练')
    await flushPromises()
    expect(apiGet.mock.calls[0][1].category).toBe('模型训练')
    expect(apiGet.mock.calls[0][1].offset).toBe(0)

    // 来源筛选：内置
    const sourceSelect = wrapper.findAllComponents({ name: 'ElSelect' })[1]
    sourceSelect.vm.$emit('update:modelValue', 'true')
    sourceSelect.vm.$emit('change', 'true')
    await flushPromises()
    expect(apiGet.mock.calls.at(-1)![1].is_builtin).toBe(true)
  })

  it('搜索关键词触发带 search 参数的查询', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(TemplateLibrary)
    apiGet.mockClear()
    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('xgboost')
    await input.trigger('keyup.enter')
    await flushPromises()
    expect(apiGet.mock.calls[0][1].search).toBe('xgboost')
  })

  it('内置模板不显示删除按钮', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(TemplateLibrary)
    const cards = wrapper.findAll('.template-card')
    expect(cards[0].text()).not.toContain('删除') // t-1 内置
    expect(cards[1].text()).toContain('删除') // t-2 用户模板
  })

  it('删除用户模板调用删除接口并刷新', async () => {
    mockApiSuccess()
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(TemplateLibrary)

    const popconfirm = wrapper.findAllComponents({ name: 'ElPopconfirm' })[0]
    popconfirm.vm.$emit('confirm')
    await flushPromises()
    expect(apiDelete).toHaveBeenCalledWith('/templates/t-2')
  })

  it('使用模板跳转训练创建页并携带模板 ID', async () => {
    mockApiSuccess()
    const { wrapper, router } = await mountPage(TemplateLibrary)
    const useBtn = wrapper.findAll('.template-card')[1].findAll('button').find((b: any) => b.text().includes('使用模板'))
    await useBtn!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/training/create')
    expect(router.currentRoute.value.query.template).toBe('t-2')
  })

  it('筛选无结果时展示"没有匹配"与清除筛选', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: [], total: 0 }) : Promise.resolve({ categories: [] })
    )
    const { wrapper } = await mountPage(TemplateLibrary)
    // 无筛选 → 引导空态
    expect(wrapper.text()).toContain('暂无模板')
    // 设置筛选后仍为空 → 无匹配
    const categorySelect = wrapper.findComponent({ name: 'ElSelect' })
    categorySelect.vm.$emit('update:modelValue', '不存在的分类')
    categorySelect.vm.$emit('change', '不存在的分类')
    await flushPromises()
    expect(wrapper.text()).toContain('没有匹配的模板')
  })

  it('加载失败时展示错误态并可重试', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.reject(new Error('后端 502')) : Promise.resolve({ categories: [] })
    )
    const { wrapper } = await mountPage(TemplateLibrary)
    expect(wrapper.text()).toContain('模板加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: TEMPLATES, total: 2 }) : Promise.resolve({ categories: [] })
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('XGBoost 分类')
  })
})
