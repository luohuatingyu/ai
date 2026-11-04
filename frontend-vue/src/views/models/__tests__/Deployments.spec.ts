import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { mountPage } from '@/test/mountPage'
import Deployments from '../../models/Deployments.vue'

const { apiGet, apiDelete } = vi.hoisted(() => ({ apiGet: vi.fn(), apiDelete: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const DEPLOYMENTS = [
  { id: 'dep-1', model_id: 'm-1', model_name: 'iris-rf', model_version: 'v1', endpoint_name: 'iris-api', status: 'running', config: { replicas: 2 }, created_at: 1700000000 },
  { id: 'dep-2', model_id: 'm-2', model_name: 'sales-xgb', model_version: 'v3', endpoint_name: '', status: 'stopped', config: {}, created_at: 1700001000 },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) =>
    url === '/models/deployments' ? Promise.resolve({ deployments: DEPLOYMENTS, total: 2 }) : Promise.resolve({})
  )
}

beforeEach(() => {
  apiGet.mockReset()
  apiDelete.mockReset()
})

describe('Deployments', () => {
  it('渲染部署行：模型链接、端点、状态与副本数', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Deployments)
    const text = wrapper.text()
    expect(text).toContain('dep-1')
    expect(text).toContain('iris-rf')
    expect(text).toContain('v1')
    expect(text).toContain('iris-api')
    expect(text).toContain('运行中')
    expect(text).toContain('2') // 副本数
    expect(text).toContain('共 2 个部署实例')
  })

  it('已停止的部署不显示停止按钮', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Deployments)
    const stoppedRow = wrapper.findAll('.el-table__row')[1]
    // 状态标签显示"已停止"，但不应存在"停止"操作按钮
    expect(stoppedRow.text()).toContain('已停止')
    const stopBtn = stoppedRow.findAll('button').find((b: any) => b.text() === '停止')
    expect(stopBtn).toBeUndefined()
  })

  it('停止部署带确认并调用 DELETE', async () => {
    mockApiSuccess()
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(Deployments)

    const runningRow = wrapper.findAll('.el-table__row')[0]
    const stopBtn = runningRow.findAll('button').find((b: any) => b.text().includes('停止'))
    await stopBtn!.trigger('click')
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('dep-1')
    expect(apiDelete).toHaveBeenCalledWith('/models/deployments/dep-1')
  })

  it('状态筛选携带参数查询', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(Deployments)
    apiGet.mockClear()

    const select = wrapper.findComponent({ name: 'ElSelect' })
    select.vm.$emit('update:modelValue', 'running')
    select.vm.$emit('change', 'running')
    await flushPromises()
    expect(apiGet.mock.calls[0][1].status).toBe('running')
    expect(apiGet.mock.calls[0][1].offset).toBe(0)
  })

  it('空列表展示引导空态', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/models/deployments' ? Promise.resolve({ deployments: [], total: 0 }) : Promise.resolve({})
    )
    const { wrapper } = await mountPage(Deployments)
    expect(wrapper.text()).toContain('暂无部署记录')
    expect(wrapper.text()).toContain('前往模型列表')
  })

  it('加载失败展示可重试错误态，重试成功后渲染表格', async () => {
    apiGet.mockRejectedValue(new Error('后端 502'))
    const { wrapper } = await mountPage(Deployments)
    expect(wrapper.text()).toContain('部署记录加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/models/deployments' ? Promise.resolve({ deployments: DEPLOYMENTS, total: 2 }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('dep-1')
  })
})
