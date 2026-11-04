import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import PipelineRuns from '../../pipelines/PipelineRuns.vue'

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

const PIPELINES = [
  { id: 'p-1', name: '销量预测' },
  { id: 'p-2', name: '日报生成' },
]

const RUNS = [
  { id: 'run-1', trigger: 'manual', status: 'completed', started_at: 1700000000, finished_at: 1700000900, error: null },
  { id: 'run-2', trigger: 'scheduled', status: 'failed', started_at: 1700003000, finished_at: 1700003060, error: '步骤 model_train 失败' },
  { id: 'run-3', trigger: 'api', status: 'running', started_at: 1700005000, finished_at: null, error: null },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/pipelines') return Promise.resolve({ pipelines: PIPELINES })
    if (url === '/pipelines/p-1/runs') return Promise.resolve({ runs: RUNS, total: 3 })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
})

describe('PipelineRuns', () => {
  it('默认选中第一个 Pipeline 并渲染执行记录（触发方式/状态/耗时）', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineRuns)
    const text = wrapper.text()
    expect(text).toContain('run-1')
    expect(text).toContain('手动')
    expect(text).toContain('已完成')
    expect(text).toContain('失败')
    expect(text).toContain('15分') // 60 秒耗时
    expect(text).toContain('步骤 model_train 失败')
    expect(text).toContain('共 3 次执行')
  })

  it('支持 ?pipeline= 直达指定 Pipeline', async () => {
    mockApiSuccess()
    await mountPage(PipelineRuns, { path: '/pipelines/runs?pipeline=p-2' })
    // 查询了 p-2 的执行历史
    expect(apiGet).toHaveBeenCalledWith('/pipelines/p-2/runs', expect.anything())
  })

  it('切换 Pipeline 重新查询并回到第一页', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineRuns)
    apiGet.mockClear()

    const select = wrapper.findComponent({ name: 'ElSelect' })
    select.vm.$emit('update:modelValue', 'p-2')
    select.vm.$emit('change', 'p-2')
    await flushPromises()

    const calls = apiGet.mock.calls.filter((c) => String(c[0]).includes('/runs'))
    expect(calls[0][0]).toBe('/pipelines/p-2/runs')
    expect(calls[0][1].offset).toBe(0)
  })

  it('运行中的执行耗时列显示 "-"', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineRuns)
    const runningRow = wrapper.findAll('.el-table__row')[2]
    expect(runningRow.text()).toContain('运行中')
    expect(runningRow.text()).toContain('-')
  })

  it('执行历史加载失败展示可重试错误态', async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === '/pipelines') return Promise.resolve({ pipelines: PIPELINES })
      return Promise.reject(new Error('后端 502'))
    })
    const { wrapper } = await mountPage(PipelineRuns)
    expect(wrapper.text()).toContain('执行历史加载失败')

    apiGet.mockImplementation((url: string) => {
      if (url === '/pipelines') return Promise.resolve({ pipelines: PIPELINES })
      if (url === '/pipelines/p-1/runs') return Promise.resolve({ runs: RUNS, total: 3 })
      return Promise.resolve({})
    })
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('run-1')
  })
})
