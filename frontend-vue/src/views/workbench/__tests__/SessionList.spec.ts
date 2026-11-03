import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { mountPage } from '@/test/mountPage'
import SessionList from '../../workbench/SessionList.vue'

const { apiGet, apiPost, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const SESSIONS = [
  { id: 's-1', name: '销量实验', status: 'running', kernel_type: 'python', bound_workers: ['w1'], last_activity: 1700000000 },
  { id: 's-2', name: '文本分类', status: 'idle', kernel_type: 'python', bound_workers: [], last_activity: 1700005000 },
  { id: 's-3', name: '异常会话', status: 'error', kernel_type: 'bash', bound_workers: [], last_activity: 0 },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/workbench/sessions') return Promise.resolve({ sessions: SESSIONS })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiDelete.mockReset()
  vi.restoreAllMocks()
})

describe('SessionList', () => {
  it('渲染会话卡片与中文状态标签', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(SessionList)
    expect(wrapper.text()).toContain('销量实验')
    expect(wrapper.text()).toContain('运行中')
    expect(wrapper.text()).toContain('空闲')
    expect(wrapper.text()).toContain('错误')
  })

  it('状态汇总卡片按状态聚合数量', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(SessionList)
    const statCards = wrapper.findAll('.stat-card')
    expect(statCards).toHaveLength(4)
    expect(statCards[0].text()).toContain('3') // 全部
    expect(statCards[1].text()).toContain('1') // 运行中
    expect(statCards[3].text()).toContain('1') // 异常
  })

  it('运行中会话点击后调用停止内核，非运行会话调用启动内核', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({})
    const { wrapper } = await mountPage(SessionList)

    // 第一张卡为 running → 停止
    let toggleBtn = wrapper.findAll('.session-card')[0].findAll('button').find((b) => b.text().includes('停止内核'))
    await toggleBtn!.trigger('click')
    await flushPromises()
    expect(apiPost).toHaveBeenCalledWith('/workbench/sessions/s-1/kernel/stop')

    // 第二张卡为 idle → 启动
    toggleBtn = wrapper.findAll('.session-card')[1].findAll('button').find((b) => b.text().includes('启动内核'))
    await toggleBtn!.trigger('click')
    await flushPromises()
    expect(apiPost).toHaveBeenCalledWith('/workbench/sessions/s-2/kernel/start')
  })

  it('删除经名称确认后调用删除接口（携带 force）', async () => {
    mockApiSuccess()
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(SessionList)

    const dropdown = wrapper.findAllComponents({ name: 'ElDropdown' })[0]
    dropdown.vm.$emit('command', 'delete', SESSIONS[0])
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('销量实验')
    expect(apiDelete).toHaveBeenCalledWith('/workbench/sessions/s-1', { params: { force: 'true' } })
  })

  it('复制会话以"副本"命名创建', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ session_id: 's-new' })
    const { wrapper } = await mountPage(SessionList)

    const dropdown = wrapper.findAllComponents({ name: 'ElDropdown' })[0]
    dropdown.vm.$emit('command', 'duplicate', SESSIONS[0])
    await flushPromises()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/workbench/sessions')
    expect(payload.name).toBe('销量实验 (副本)')
    expect(payload.kernel_type).toBe('python')
  })

  it('新建会话按钮跳转到独立创建页', async () => {
    mockApiSuccess()
    const { wrapper, router } = await mountPage(SessionList)
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('新建会话'))
    await createBtn!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/workbench/new')
  })

  it('列表失败时展示可重试错误态，重试成功后渲染卡片', async () => {
    apiGet.mockImplementation(() => Promise.reject(new Error('后端 502')))
    const { wrapper } = await mountPage(SessionList)
    expect(wrapper.text()).toContain('会话加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/workbench/sessions' ? Promise.resolve({ sessions: SESSIONS }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('销量实验')
  })

  it('列表为空时展示引导空态', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/workbench/sessions' ? Promise.resolve({ sessions: [] }) : Promise.resolve({})
    )
    const { wrapper } = await mountPage(SessionList)
    expect(wrapper.text()).toContain('暂无会话')
    expect(wrapper.text()).toContain('创建第一个会话')
  })
})
