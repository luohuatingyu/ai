import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import MyTemplates from '../../templates/MyTemplates.vue'

const { apiGet, apiDelete } = vi.hoisted(() => ({ apiGet: vi.fn(), apiDelete: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const MY_TEMPLATES = [
  { id: 't-1', name: '销量预测模板', category: '模型训练', task_type: 'regression', description: '回归模板', parameters: [{}, {}], updated_at: 1700000000 },
  { id: 't-2', name: '时序模板', category: '时序', task_type: 'time_series', description: '', parameters: [], updated_at: 0 },
]

beforeEach(() => {
  apiGet.mockReset()
  apiDelete.mockReset()
})

describe('MyTemplates', () => {
  it('仅拉取用户模板（is_builtin=false）并渲染表格', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: MY_TEMPLATES }) : Promise.resolve({})
    )
    const { wrapper } = await mountPage(MyTemplates)
    // 首个请求必须带 is_builtin: false
    expect(apiGet.mock.calls[0]).toEqual(['/templates', { is_builtin: false, limit: 100 }])
    expect(wrapper.text()).toContain('销量预测模板')
    expect(wrapper.text()).toContain('回归')
    expect(wrapper.text()).toContain('时间序列')
    expect(wrapper.text()).toContain('2') // 参数数
  })

  it('删除经 Popconfirm 确认后调用删除接口并刷新', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: MY_TEMPLATES }) : Promise.resolve({})
    )
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(MyTemplates)

    const popconfirm = wrapper.findComponent({ name: 'ElPopconfirm' })
    popconfirm.vm.$emit('confirm')
    await flushPromises()
    expect(apiDelete).toHaveBeenCalledWith('/templates/t-1')
    // 删除后重新拉取
    expect(apiGet).toHaveBeenCalledTimes(2)
  })

  it('编辑按钮携带模板 ID 跳转', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: MY_TEMPLATES }) : Promise.resolve({})
    )
    const { wrapper, router } = await mountPage(MyTemplates)
    const editBtn = wrapper.findAll('button').find((b: any) => b.text() === '编辑')
    await editBtn!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.id).toBe('t-1')
  })

  it('空列表展示引导空态', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: [] }) : Promise.resolve({})
    )
    const { wrapper } = await mountPage(MyTemplates)
    expect(wrapper.text()).toContain('暂无用户模板')
    expect(wrapper.text()).toContain('创建第一个模板')
  })

  it('失败展示可重试错误态，重试成功后渲染表格', async () => {
    apiGet.mockImplementation(() => Promise.reject(new Error('后端 502')))
    const { wrapper } = await mountPage(MyTemplates)
    expect(wrapper.text()).toContain('模板加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/templates' ? Promise.resolve({ templates: MY_TEMPLATES }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('销量预测模板')
  })
})
