import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import PipelineList from '../../pipelines/PipelineList.vue'

const { apiGet, apiDelete, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
  apiPost: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const PIPELINES = [
  { id: 'p-1', name: '销量预测', description: '全流程', steps_count: 3, schedule: '0 2 * * *', status: 'active', created_at: 1700000000 },
  { id: 'p-2', name: '日报生成', description: '', steps_count: 2, schedule: '', status: 'draft', created_at: 1700001000 },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) =>
    url === '/pipelines' ? Promise.resolve({ pipelines: PIPELINES }) : Promise.resolve({})
  )
}

beforeEach(() => {
  apiGet.mockReset()
  apiDelete.mockReset()
  apiPost.mockReset()
})

describe('PipelineList', () => {
  it('渲染 Pipeline 行：调度、状态与步骤数', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineList)
    const text = wrapper.text()
    expect(text).toContain('销量预测')
    expect(text).toContain('0 2 * * *')
    expect(text).toContain('已激活')
    expect(text).toContain('草稿')
    expect(text).toContain('3')
    expect(text).toContain('共 2 个 Pipeline')
  })

  it('仅已激活的 Pipeline 显示运行按钮，运行带确认并跳转执行详情', async () => {
    mockApiSuccess()
    const { ElMessageBox } = await import('element-plus')
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiPost.mockResolvedValue({ run_id: 'run-1' })
    const { wrapper, router } = await mountPage(PipelineList)

    const rows = wrapper.findAll('.el-table__row')
    expect(rows[0].text()).toContain('运行')
    expect(rows[1].text()).not.toContain('运行')

    const runBtn = rows[0].findAll('button').find((b: any) => b.text().includes('运行'))
    await runBtn!.trigger('click')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith('/pipelines/p-1/run', { trigger: 'manual', parameters: {} })
    expect(router.currentRoute.value.path).toBe('/pipelines/run/p-1/run-1')
  })

  it('删除经 Popconfirm 确认后调用 DELETE 并刷新', async () => {
    mockApiSuccess()
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(PipelineList)

    const popconfirm = wrapper.findComponent({ name: 'ElPopconfirm' })
    popconfirm.vm.$emit('confirm')
    await flushPromises()
    expect(apiDelete).toHaveBeenCalledWith('/pipelines/p-1')
  })

  it('状态筛选仅展示匹配项', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineList)

    const select = wrapper.findComponent({ name: 'ElSelect' })
    select.vm.$emit('update:modelValue', 'draft')
    await flushPromises()

    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('日报生成')
    expect(wrapper.text()).toContain('当前筛选 1 个')
  })

  it('空列表展示引导空态', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/pipelines' ? Promise.resolve({ pipelines: [] }) : Promise.resolve({})
    )
    const { wrapper } = await mountPage(PipelineList)
    expect(wrapper.text()).toContain('暂无 Pipeline')
    expect(wrapper.text()).toContain('创建 Pipeline')
  })

  it('加载失败展示可重试错误态，重试成功后渲染表格', async () => {
    apiGet.mockRejectedValue(new Error('后端 502'))
    const { wrapper } = await mountPage(PipelineList)
    expect(wrapper.text()).toContain('Pipeline 加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/pipelines' ? Promise.resolve({ pipelines: PIPELINES }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('销量预测')
  })
})
