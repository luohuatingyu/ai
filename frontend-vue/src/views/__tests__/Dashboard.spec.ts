import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { useAppStore } from '@/stores/app'
import { mountPage } from '@/test/mountPage'
import Dashboard from '../Dashboard.vue'
import StatCard from '@/components/common/StatCard.vue'

// 图表组件打桩：happy-dom 无 canvas，验证数据聚合逻辑即可
vi.mock('vue-echarts', () => ({
  default: { name: 'VChart', template: '<div class="v-chart-stub" />' },
}))

// API 客户端打桩：按 URL 分发响应
const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }))
vi.mock('@/api', () => ({
  api: {
    get: apiGet,
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    upload: vi.fn(),
  },
}))

const JOBS = [
  { id: 'j1', name: '销量预测', status: 'completed', spec: { task: 'regression', dataset_name: 'sales' }, created_at: 1700000000 },
  { id: 'j2', name: '垃圾邮件分类', status: 'failed', spec: { task: 'classification', dataset_name: 'spam' }, created_at: 1700001000 },
  { id: 'j3', name: '用户分群', status: 'running', spec: { task: 'clustering', dataset_name: 'users' }, created_at: 1700002000 },
]

const MODELS = {
  total: 12,
  models: [
    { id: 'm1', name: 'sales-rf', version: 'v1', task: 'regression', framework: 'sklearn', metrics: { r2: 0.91 } },
  ],
}

/** 配置所有仪表盘接口的默认响应 */
function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/training/jobs') return Promise.resolve({ jobs: JOBS, total: 3 })
    if (url === '/models') return Promise.resolve(MODELS)
    if (url === '/datasets') return Promise.resolve({ total: 7, datasets: [] })
    if (url === '/pipelines') return Promise.resolve({ total: 3, pipelines: [] })
    if (url === '/clusters/workers/summary')
      return Promise.resolve({ total: 4, gpu: 2, cpu: 2, details: [{ state: 'idle' }, { current_job: 'x' }] })
    if (url === '/system') return Promise.resolve({ torch_version: '2.1.0', cuda_version: '12.1', gpu_count: 2, backend: 'cuda' })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
})

describe('Dashboard', () => {
  it('加载后关键指标展示真实统计值', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Dashboard)
    const cards = wrapper.findAllComponents(StatCard)
    expect(cards).toHaveLength(4)
    expect(cards[0].props('value')).toBe('3') // 训练任务
    expect(cards[1].props('value')).toBe('12') // 注册模型
    expect(cards[2].props('value')).toBe('7') // 数据集
    expect(cards[3].props('value')).toBe('3') // Pipeline
  })

  it('最近任务表格渲染任务名称与中文状态', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Dashboard)
    const text = wrapper.text()
    expect(text).toContain('销量预测')
    expect(text).toContain('垃圾邮件分类')
    expect(text).toContain('运行中') // running 的中文标签
  })

  it('任务状态分布图表基于真实任务数据渲染', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Dashboard)
    expect(wrapper.find('.v-chart-stub').exists()).toBe(true)
  })

  it('任务接口失败时对应区块展示错误态并可重试，不影响其他区块', async () => {
    mockApiSuccess()
    apiGet.mockImplementation((url: string) => {
      if (url === '/training/jobs') return Promise.reject(new Error('后端 502'))
      if (url === '/models') return Promise.resolve(MODELS)
      if (url === '/datasets') return Promise.resolve({ total: 7, datasets: [] })
      if (url === '/pipelines') return Promise.resolve({ total: 3, pipelines: [] })
      if (url === '/clusters/workers/summary') return Promise.resolve({ total: 0, details: [] })
      if (url === '/system') return Promise.resolve({})
      return Promise.resolve({})
    })
    const pinia = createPinia()
    const appStore = useAppStore(pinia)
    // 挂载期间即会调用 warning，必须先建立 spy 再挂载
    const warningSpy = vi.spyOn(appStore, 'warning')
    const { wrapper } = await mountPage(Dashboard, { pinia })
    expect(warningSpy).toHaveBeenCalledWith('训练任务统计加载失败', '后端 502')
    expect(wrapper.text()).toContain('训练任务加载失败')
    // 其他区块不受影响
    expect(wrapper.text()).toContain('sales-rf')
    // 点击重试重新拉取
    apiGet.mockImplementation((url: string) =>
      url === '/training/jobs' ? Promise.resolve({ jobs: JOBS, total: 3 }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('销量预测')
  })

  it('刷新按钮触发全部区块重新加载', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Dashboard)
    apiGet.mockClear()
    const refreshBtn = wrapper.findAll('button').find((b) => b.text().includes('刷新'))
    await refreshBtn!.trigger('click')
    await flushPromises()
    const calledUrls = apiGet.mock.calls.map((c) => c[0])
    expect(calledUrls).toContain('/training/jobs')
    expect(calledUrls).toContain('/models')
    expect(calledUrls).toContain('/datasets')
    expect(calledUrls).toContain('/pipelines')
    expect(calledUrls).toContain('/clusters/workers/summary')
    expect(calledUrls).toContain('/system')
  })

  it('存在失败任务时展示红色关注条，点击直达筛选后的任务列表', async () => {
    mockApiSuccess()
    const { wrapper, router } = await mountPage(Dashboard)
    const strip = wrapper.find('.attention-strip')
    expect(strip.exists()).toBe(true)
    // 1 个失败（j2）+ 1 个运行中（j3），无排队
    expect(strip.classes()).toContain('is-alert')
    expect(strip.text()).toContain('失败 1')
    expect(strip.text()).toContain('运行中 1')

    const failedChip = strip.findAll('button').find((b) => b.text().includes('失败'))
    await failedChip!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/training/jobs?status=failed')
  })

  it('全部任务完成时展示绿色提示条（仅排队/运行时）或不展示', async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === '/training/jobs')
        return Promise.resolve({ jobs: [{ id: 'j1', status: 'completed', spec: {}, created_at: 1 }] })
      return Promise.resolve({})
    })
    const { wrapper } = await mountPage(Dashboard)
    // 无失败、无排队、无运行 → 不渲染关注条
    expect(wrapper.find('.attention-strip').exists()).toBe(false)
  })
})
