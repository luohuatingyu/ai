import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { mountPage } from '@/test/mountPage'
import JobList from '../../training/JobList.vue'

const { apiGet, apiPost, apiDelete } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn(), apiDelete: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const JOBS = [
  { id: 'j-1', status: 'running', device: 'cuda:0', created_at: 1700000000, spec: { task: 'classification', dataset_name: 'iris' } },
  { id: 'j-2', status: 'completed', created_at: 1700001000, spec: { task: 'regression', dataset_id: 'ds-9' }, resource_hint: { require_gpu: true } },
  { id: 'j-3', status: 'failed', created_at: 1700002000, spec: { task: 'clustering' }, resource_hint: { parallel_workers: 2 } },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) =>
    url === '/training/jobs' ? Promise.resolve({ jobs: JOBS }) : Promise.resolve({})
  )
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiDelete.mockReset()
  vi.restoreAllMocks()
})

describe('JobList', () => {
  it('渲染任务行：类型中文、数据集、状态标签与设备', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobList)
    const text = wrapper.text()
    expect(text).toContain('j-1')
    expect(text).toContain('分类')
    expect(text).toContain('iris')
    expect(text).toContain('运行中')
    expect(text).toContain('cuda:0')
    expect(text).toContain('联合训练 ×2')
    expect(text).toContain('GPU')
  })

  it('状态汇总卡片聚合数量', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobList)
    const cards = wrapper.findAll('.stat-card')
    expect(cards[0].text()).toContain('3') // 全部
    expect(cards[1].text()).toContain('1') // 进行中（running 1 + queued 0）
    expect(cards[2].text()).toContain('1') // 已完成
    expect(cards[3].text()).toContain('1') // 失败/取消（1 个 failed）
  })

  it('状态筛选仅影响表格展示且提示筛选计数', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobList)
    // 筛选 running（第一个 ElSelect 在 FilterBar 中）
    await wrapper.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'running')
    await flushPromises()
    const rows = wrapper.findAll('.el-table__row')
    expect(rows).toHaveLength(1)
    expect(wrapper.text()).toContain('当前筛选 1 个')
  })

  it('取消任务经确认后调用取消接口', async () => {
    mockApiSuccess()
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiPost.mockResolvedValue({})
    const { wrapper } = await mountPage(JobList)

    const runningRow = wrapper.findAll('.el-table__row')[0]
    const cancelBtn = runningRow.findAll('button').find((b: any) => b.text().includes('取消'))
    await cancelBtn!.trigger('click')
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('j-1')
    expect(apiPost).toHaveBeenCalledWith('/training/jobs/j-1/cancel')
  })

  it('删除任务经确认后调用删除接口', async () => {
    mockApiSuccess()
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(JobList)

    // 第三行 failed → 有删除按钮
    const failedRow = wrapper.findAll('.el-table__row')[2]
    const deleteBtn = failedRow.findAll('button').find((b: any) => b.text().includes('删除'))
    await deleteBtn!.trigger('click')
    await flushPromises()

    expect(apiDelete).toHaveBeenCalledWith('/training/jobs/j-3')
  })

  it('加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('后端 502'))
    const { wrapper } = await mountPage(JobList)
    expect(wrapper.text()).toContain('任务加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/training/jobs' ? Promise.resolve({ jobs: JOBS }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('j-1')
  })
})
