import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import SessionDetail from '../../workbench/SessionDetail.vue'

// Monaco Editor 通过 vitest.config.ts 别名指向轻量桩（src/test/monaco-stub.ts），无需真实编辑器

const { apiGet, apiPost, apiPut } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: apiPut, delete: vi.fn(), upload: vi.fn() },
}))

const SESSION = {
  id: 's-1',
  name: '销量实验',
  status: 'running',
  kernel_type: 'python',
  bound_workers: ['w1'],
  cells: [
    { id: 'c1', cell_type: 'code', source: 'print(1)', outputs: [], execution_count: 1 },
    { id: 'c2', cell_type: 'markdown', source: '# 标题', outputs: [], execution_count: null },
  ],
}

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/workbench/sessions/s-1') return Promise.resolve(SESSION)
    if (url === '/workbench/sessions/s-1/variables') return Promise.resolve({ variables: { df: { rows: 100 } } })
    return Promise.resolve({})
  })
}

const routes = [{ path: '/workbench/session/:id', name: 'WorkbenchSession', component: SessionDetail }]

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
})

describe('SessionDetail', () => {
  it('渲染会话名称、状态与运行信息', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })
    expect(wrapper.text()).toContain('销量实验')
    expect(wrapper.text()).toContain('运行中')
    expect(wrapper.text()).toContain('2 个（已执行 1）')
    expect(wrapper.text()).toContain('python')
    expect(wrapper.text()).toContain('1 个 Worker')
  })

  it('添加代码单元格后单元格数量更新', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })
    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('代码'))
    await addBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('3 个（已执行 1）')
  })

  it('运行单元格调用执行接口并携带单元格载荷', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ success: true, outputs: [], execution_count: 2, variables: {} })
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })

    const runBtn = wrapper.findAll('.code-cell')[0].findAll('button').find((b) => b.text().includes('运行'))
    await runBtn!.trigger('click')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith('/workbench/sessions/s-1/cells/execute', {
      cell_id: 'c1',
      cell_index: 0,
      code: 'print(1)',
      cell_type: 'code',
      mode: 'single',
    })
  })

  it('执行结果写入单元格输出与执行计数', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({
      success: true,
      outputs: [{ output_type: 'stream', name: 'stdout', text: '1\n' }],
      execution_count: 2,
      variables: { x: 1 },
    })
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })

    const runBtn = wrapper.findAll('.code-cell')[0].findAll('button').find((b) => b.text().includes('运行'))
    await runBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('1\n')
    expect(wrapper.text()).toContain('[2]')
  })

  it('执行失败时单元格输出错误信息', async () => {
    mockApiSuccess()
    apiPost.mockRejectedValue(new Error('内核超时'))
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })

    const runBtn = wrapper.findAll('.code-cell')[0].findAll('button').find((b) => b.text().includes('运行'))
    await runBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('内核超时')
  })

  it('内核切换调用对应的启动/停止接口', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({})
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })

    const stopBtn = wrapper.findAll('button').find((b) => b.text().includes('停止内核'))
    await stopBtn!.trigger('click')
    await flushPromises()
    expect(apiPost).toHaveBeenCalledWith('/workbench/sessions/s-1/kernel/stop')
  })

  it('全部运行按顺序执行所有代码单元格', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ success: true, outputs: [], execution_count: 2, variables: {} })
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })

    const runAllBtn = wrapper.findAll('button').find((b) => b.text().includes('全部运行'))
    await runAllBtn!.trigger('click')
    await flushPromises()

    const execCalls = apiPost.mock.calls.filter((c) => c[0].endsWith('/cells/execute'))
    expect(execCalls).toHaveLength(1) // 只有 c1 是代码单元格
    expect(execCalls[0][1].cell_id).toBe('c1')
  })

  it('变量查看器可通过工具栏打开并展示变量', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })
    const varBtn = wrapper.findAll('button').find((b) => b.text().includes('变量'))
    await varBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('变量查看器')
    expect(wrapper.text()).toContain('df')
  })

  it('加载失败时跳回会话列表', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/workbench/sessions/s-1' ? Promise.reject(new Error('会话不存在')) : Promise.resolve({})
    )
    const { router } = await mountPage(SessionDetail, { routes, path: '/workbench/session/s-1' })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/workbench/sessions')
  })
})
