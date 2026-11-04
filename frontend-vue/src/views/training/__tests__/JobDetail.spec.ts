import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import JobDetail from '../../training/JobDetail.vue'

// 图表组件打桩：happy-dom 无 canvas
vi.mock('vue-echarts', () => ({
  default: { name: 'VChart', template: '<div class="v-chart-stub" />' },
}))

const { apiGet, apiPost } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

const JOB = {
  id: 'j-1',
  status: 'completed',
  device: 'cuda:0',
  worker_id: 'w1',
  created_at: 1700000000,
  finished_at: 1700003600,
  spec: { task: 'classification', dataset_name: 'iris', target: 'species' },
  progress: { epoch: 20, epochs_total: 20, percent: 100 },
  metrics: [
    { epoch: 1, accuracy: 0.72, loss: 0.9 },
    { epoch: 2, accuracy: 0.85, loss: 0.6 },
  ],
}

const FAILED_JOB = {
  ...JOB,
  id: 'j-2',
  status: 'failed',
  last_error: 'CUDA out of memory',
  progress: { epoch: 5, epochs_total: 20, percent: 25 },
}

const routes = [{ path: '/training/detail/:id', component: JobDetail }]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/training/jobs/j-1') return Promise.resolve(JOB)
    if (url === '/training/jobs/j-1/logs') return Promise.resolve({ lines: ['epoch 1 done', 'ERROR x', 'epoch 2 done'] })
    if (url === '/training/jobs/j-2') return Promise.resolve(FAILED_JOB)
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
})

describe('JobDetail', () => {
  it('渲染状态进度、基本信息与指标', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })
    const text = wrapper.text()
    expect(text).toContain('已完成')
    // 进度条渲染且进度值为 100（happy-dom 下 EP 进度不渲染百分比文本，用 aria 值断言）
    expect(wrapper.find('.el-progress').exists()).toBe(true)
    expect(wrapper.find('.el-progress').attributes('aria-valuenow')).toBe('100')
    expect(text).toContain('Epoch 20 / 20')
    expect(text).toContain('分类')
    expect(text).toContain('iris')
    expect(text).toContain('species')
    expect(text).toContain('cuda:0')
  })

  it('指标趋势图表基于真实指标渲染', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })
    expect(wrapper.find('.v-chart-stub').exists()).toBe(true)
    // 指标明细表渲染两列指标
    expect(wrapper.text()).toContain('accuracy')
    expect(wrapper.text()).toContain('loss')
    expect(wrapper.text()).toContain('0.8500') // 4 位小数格式化
  })

  it('失败任务顶部展示错误告警', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-2' })
    expect(wrapper.text()).toContain('任务失败：CUDA out of memory')
    // 失败任务不显示下载模型按钮
    expect(wrapper.text()).not.toContain('下载模型')
  })

  it('取消任务调用取消接口并刷新详情', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/training/jobs/j-1' ? Promise.resolve({ ...JOB, status: 'running' }) : Promise.resolve({})
    )
    apiPost.mockResolvedValue({})
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })

    const cancelBtn = wrapper.findAll('button').find((b: any) => b.text().includes('取消任务'))
    await cancelBtn!.trigger('click')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith('/training/jobs/j-1/cancel')
    expect(apiGet).toHaveBeenCalledWith('/training/jobs/j-1') // 取消后刷新详情
  })

  it('下载模型走后端文件流接口', async () => {
    mockApiSuccess()
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null as any)
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })

    const downloadBtn = wrapper.findAll('button').find((b: any) => b.text().includes('下载模型'))
    await downloadBtn!.trigger('click')
    expect(openSpy).toHaveBeenCalledWith('/api/training/jobs/j-1/model', '_blank')
  })

  it('加载日志并支持关键词过滤', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })
    // 挂载后自动加载日志
    expect(wrapper.text()).toContain('epoch 1 done')

    // 过滤 ERROR
    const filterInput = wrapper.find('input[placeholder*="过滤日志"]')
    await filterInput.setValue('ERROR')
    await flushPromises()
    expect(wrapper.find('.log-content').text()).toBe('ERROR x')
  })

  it('任务 Hero 展示最新指标与日志入口', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })
    const hero = wrapper.find('.job-hero')
    expect(hero.exists()).toBe(true)
    // Hero 提供日志入口按钮
    expect(hero.findAll('button').some((b) => b.text().includes('查看日志'))).toBe(true)
    // 关键指标 chips 取最新 epoch（epoch 2: accuracy 0.85 / loss 0.6）
    expect(hero.text()).toContain('accuracy')
    expect(hero.text()).toContain('0.8500')
    expect(hero.text()).toContain('loss')
    expect(hero.text()).toContain('0.6000')
  })

  it('详情加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('任务不存在'))
    const { wrapper } = await mountPage(JobDetail, { routes, path: '/training/detail/j-1' })
    expect(wrapper.text()).toContain('任务加载失败')

    apiGet.mockResolvedValue(JOB)
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已完成')
  })
})
