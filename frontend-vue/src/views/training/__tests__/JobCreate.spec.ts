import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import JobCreate from '../../training/JobCreate.vue'

const { apiGet, apiPost } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/datasets') return Promise.resolve({ datasets: [{ id: 'ds-1', name: '销量数据', rows: 1200 }] })
    if (url === '/datasets/ds-1') return Promise.resolve({ columns: ['date', 'sales', 'region'] })
    if (url === '/templates/tpl-1')
      return Promise.resolve({
        id: 'tpl-1', name: '销量回归模板', task_type: 'regression',
        parameters: [{ name: 'seed', default: 42 }, { name: 'test_split', default: 0.2 }, { name: 'empty', default: null }],
      })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
})

describe('JobCreate', () => {
  it('数据集切换后拉取列信息并清空目标列', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobCreate)
    apiGet.mockClear()

    // 数据集 select 是第一个 ElSelect：update:modelValue + change（模拟用户选择）
    const datasetSelect = wrapper.findComponent({ name: 'ElSelect' })
    datasetSelect.vm.$emit('update:modelValue', 'ds-1')
    datasetSelect.vm.$emit('change', 'ds-1')
    await flushPromises()

    expect(apiGet).toHaveBeenCalledWith('/datasets/ds-1')
    // 摘要展示数据集名称
    expect(wrapper.text()).toContain('销量数据')
  })

  it('切换任务类型卡片后模型选项随之变化', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobCreate)

    // 默认分类：包含 XGBoost
    expect(wrapper.text()).toContain('算法与参数')
    const taskCards = wrapper.findAll('.task-option')
    expect(taskCards).toHaveLength(4)

    // 切到聚类 → 模型仅剩 MLP，且显示聚类数
    await taskCards[2].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('聚类数')
    expect(wrapper.text()).toContain('未选择') // 目标列隐藏，摘要切换为聚类数
  })

  it('模板跳转预填：任务类型映射与参数默认值合并进高级参数', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobCreate, { path: '/training/create?template=tpl-1' })
    await flushPromises()

    expect(wrapper.text()).toContain('模板：销量回归模板')
    const textareas = wrapper.findAll('textarea')
    const advancedJson = (textareas[0].element as HTMLTextAreaElement).value
    const parsed = JSON.parse(advancedJson)
    expect(parsed).toEqual({ seed: 42, test_split: 0.2 }) // default 为 null 的参数被跳过
  })

  it('清除模板标签后回到无模板状态', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobCreate, { path: '/training/create?template=tpl-1' })
    await flushPromises()

    const closeIcon = wrapper.find('.el-tag__close')
    await closeIcon.trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('模板：销量回归模板')
  })

  it('提交载荷按任务类型组装：分类带 target，聚类带 n_clusters', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ job_id: 'job-new' })
    const { wrapper, router } = await mountPage(JobCreate)

    // 选择数据集与目标列
    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    await selects[0].vm.$emit('update:modelValue', 'ds-1')
    await flushPromises()
    // 目标列 select（第二个）
    await selects[1].vm.$emit('update:modelValue', 'sales')
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建任务'))
    await submitBtn!.trigger('click')
    await flushPromises()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/training/jobs')
    expect(payload.dataset_id).toBe('ds-1')
    expect(payload.task).toBe('classification')
    expect(payload.target).toBe('sales')
    expect(payload.model_type).toBe('mlp')
    expect(payload.epochs).toBe(20)
    expect(payload.device_backend).toBe('auto')
    expect(payload).not.toHaveProperty('n_clusters')
    expect(payload).not.toHaveProperty('time_column')
    expect(router.currentRoute.value.path).toBe('/training/jobs')
  })

  it('聚类任务提交带 n_clusters 不带 target', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ job_id: 'job-cls' })
    const { wrapper } = await mountPage(JobCreate)

    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    await selects[0].vm.$emit('update:modelValue', 'ds-1')
    await flushPromises()

    await wrapper.findAll('.task-option')[2].trigger('click') // 聚类
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建任务'))
    await submitBtn!.trigger('click')
    await flushPromises()

    const [, payload] = apiPost.mock.calls[0]
    expect(payload.task).toBe('clustering')
    expect(payload.n_clusters).toBe(4)
    expect(payload).not.toHaveProperty('target')
  })

  it('时间序列任务提交带 time_column', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ job_id: 'job-ts' })
    const { wrapper } = await mountPage(JobCreate)

    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    // 时间序列同时显示目标列与时间列：顺序为 [数据集, 目标列, 时间列, 模型, 后端]
    selects[0].vm.$emit('update:modelValue', 'ds-1')
    selects[0].vm.$emit('change', 'ds-1')
    await flushPromises()

    await wrapper.findAll('.task-option')[3].trigger('click') // 时间序列
    await flushPromises()
    await wrapper.findAllComponents({ name: 'ElSelect' })[1].vm.$emit('update:modelValue', 'sales') // 目标列
    await wrapper.findAllComponents({ name: 'ElSelect' })[2].vm.$emit('update:modelValue', 'date') // 时间列
    await flushPromises()

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建任务'))
    await submitBtn!.trigger('click')
    await flushPromises()

    const [, payload] = apiPost.mock.calls[0]
    expect(payload.task).toBe('time_series_forecasting')
    expect(payload.time_column).toBe('date')
    expect(payload.target).toBe('sales')
  })

  it('高级参数 JSON 非法时阻止提交并提示', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(JobCreate)

    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    await selects[0].vm.$emit('update:modelValue', 'ds-1')
    await flushPromises()
    await wrapper.findAllComponents({ name: 'ElSelect' })[1].vm.$emit('update:modelValue', 'sales')

    const textarea = wrapper.findAll('textarea')[0]
    await textarea.setValue('{invalid json')

    const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('创建任务'))
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('高级参数不是合法 JSON 对象')
    expect(apiPost).not.toHaveBeenCalled()
  })
})
