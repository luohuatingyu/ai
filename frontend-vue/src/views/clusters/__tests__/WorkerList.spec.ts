import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import WorkerList from '../../clusters/WorkerList.vue'

const { apiGet, apiPatch } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPatch: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), patch: apiPatch, delete: vi.fn(), upload: vi.fn() },
}))

const WORKERS = [
  { worker_id: 'local-gpu-0', machine_id: 'local', slot: 0, gpu: { available: true, name: 'A100', vram_mb: 81920 }, active: true, state: 'idle', occupied: false, current_job: null, heartbeat_age_sec: 5, enabled: true, managed: false },
  { worker_id: 'remote-gpu-1', machine_id: 'gpu-node-01', slot: 1, gpu: { available: true, name: 'RTX 4090', vram_mb: 24576 }, active: true, state: 'busy', occupied: true, current_job: 'j-77', heartbeat_age_sec: 30, enabled: true, managed: true },
  { worker_id: 'remote-cpu-0', machine_id: 'cpu-node-01', slot: 0, gpu: { available: false }, active: false, state: 'idle', occupied: false, current_job: null, heartbeat_age_sec: 3600, enabled: false, managed: true },
  { worker_id: 'remote-cpu-1', machine_id: 'cpu-node-01', slot: 1, gpu: { available: false }, active: true, state: 'idle', occupied: false, current_job: null, heartbeat_age_sec: 200, enabled: true, managed: true },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) =>
    url === '/clusters/workers' ? Promise.resolve({ workers: WORKERS, queue_depth: 3 }) : Promise.resolve({})
  )
}

beforeEach(() => {
  apiGet.mockReset()
  apiPatch.mockReset()
})

describe('WorkerList', () => {
  it('渲染 Worker 行：GPU 名称/显存、状态与当前任务', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(WorkerList)
    const text = wrapper.text()
    expect(text).toContain('local-gpu-0')
    expect(text).toContain('A100（80.0 GB）')
    expect(text).toContain('RTX 4090（24.0 GB）')
    expect(text).toContain('CPU')
    expect(text).toContain('空闲')
    expect(text).toContain('忙碌中')
    expect(text).toContain('离线')
    expect(text).toContain('j-77')
    expect(text).toContain('5 秒前')
  })

  it('汇总瓦片聚合：队列/在线/空闲/占用/离线', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(WorkerList)
    const tiles = wrapper.findAll('.summary-tile')
    expect(tiles).toHaveLength(5)
    expect(tiles[0].text()).toContain('3') // 队列深度
    expect(tiles[1].text()).toContain('3') // 在线
    expect(tiles[2].text()).toContain('2') // 空闲（active 且 idle）
    expect(tiles[3].text()).toContain('1') // 占用
    expect(tiles[4].text()).toContain('1') // 离线
  })

  it('在线但心跳超 2 分钟的节点标黄', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(WorkerList)
    // remote-cpu-1 在线但心跳 200 秒
    const staleCell = wrapper.findAll('.el-table__row')[3].find('.heartbeat-stale')
    expect(staleCell.exists()).toBe(true)
    expect(staleCell.text()).toContain('3 分钟前')
    // 离线节点不标黄
    expect(wrapper.findAll('.el-table__row')[2].find('.heartbeat-stale').exists()).toBe(false)
  })

  it('托管 Worker 可切换接收任务，本机内置槽禁用', async () => {
    mockApiSuccess()
    apiPatch.mockResolvedValue({ message: '已停用该计算槽' })
    const { wrapper } = await mountPage(WorkerList)

    const rows = wrapper.findAll('.el-table__row')
    // 第一行本机内置槽：开关禁用
    const localSwitch = rows[0].find('.el-switch')
    expect(localSwitch.classes()).toContain('is-disabled')

    // 第二行托管 Worker：切换后调用 PATCH
    const remoteSwitch = rows[1].find('.el-switch')
    await remoteSwitch.find('input').setValue(false)
    await flushPromises()

    expect(apiPatch).toHaveBeenCalledWith(
      '/clusters/workers/remote-gpu-1/enable',
      null,
      { params: { enabled: false } }
    )
  })

  it('加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('后端 502'))
    const { wrapper } = await mountPage(WorkerList)
    expect(wrapper.text()).toContain('Worker 加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/clusters/workers' ? Promise.resolve({ workers: WORKERS, queue_depth: 0 }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('local-gpu-0')
  })
})
