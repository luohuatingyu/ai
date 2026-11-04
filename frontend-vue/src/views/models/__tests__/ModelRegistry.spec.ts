import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { createPinia } from 'pinia'
import { mountPage } from '@/test/mountPage'
import ModelRegistry from '../../models/ModelRegistry.vue'

const { apiGet, apiDelete } = vi.hoisted(() => ({ apiGet: vi.fn(), apiDelete: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: vi.fn(), put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const MODELS = [
  { id: 'm-1', name: 'iris-rf', version: 'v1', task: 'classification', framework: 'sklearn', status: 'ready', created_at: 1700000000, metrics: { accuracy: 0.9555 } },
  { id: 'm-2', name: 'sales-xgb', version: 'v3', task: 'regression', framework: 'xgboost', status: 'archived', created_at: 1700001000, metrics: {} },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) =>
    url === '/models' ? Promise.resolve({ models: MODELS, total: 2 }) : Promise.resolve({})
  )
}

beforeEach(() => {
  apiGet.mockReset()
  apiDelete.mockReset()
})

describe('ModelRegistry', () => {
  it('默认仅请求可用（ready）模型并渲染行', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(ModelRegistry)
    // 默认状态筛选 ready
    expect(apiGet.mock.calls[0][1].status).toBe('ready')
    const text = wrapper.text()
    expect(text).toContain('iris-rf')
    expect(text).toContain('分类')
    expect(text).toContain('sklearn')
    expect(text).toContain('accuracy: 0.9555') // 4 位小数
    expect(text).toContain('可用')
    expect(text).toContain('已归档')
    expect(text).toContain('共 2 个模型')
  })

  it('搜索携带 search/task 参数并回到第一页', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(ModelRegistry)
    apiGet.mockClear()

    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('xgb')
    await input.trigger('keyup.enter')
    await flushPromises()
    expect(apiGet.mock.calls[0][1].search).toBe('xgb')
    expect(apiGet.mock.calls[0][1].offset).toBe(0)
  })

  it('归档带确认且展示名称版本，确认后调用 DELETE', async () => {
    mockApiSuccess()
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiDelete.mockResolvedValue({})
    const { wrapper } = await mountPage(ModelRegistry)

    // m-2 已归档无归档按钮，m-1 有
    const readyRow = wrapper.findAll('.el-table__row')[0]
    const archiveBtn = readyRow.findAll('button').find((b: any) => b.text().includes('归档'))
    await archiveBtn!.trigger('click')
    await flushPromises()

    expect(confirmSpy.mock.calls[0][0]).toContain('iris-rf')
    expect(confirmSpy.mock.calls[0][0]).toContain('v1')
    expect(apiDelete).toHaveBeenCalledWith('/models/m-1')
  })

  it('下载链接模式打开新窗口，本地存储模式提示路径', async () => {
    mockApiSuccess()
    const openSpy = vi.spyOn(window, 'open').mockReturnValue(null as any)
    // 挂载前建立 store spy（下载后调用 info）
    const pinia = createPinia()
    const { useAppStore } = await import('@/stores/app')
    const infoSpy = vi.spyOn(useAppStore(pinia), 'info')
    const { wrapper } = await mountPage(ModelRegistry, { pinia })

    apiGet.mockImplementation((url: string) => {
      if (url === '/models') return Promise.resolve({ models: MODELS, total: 2 })
      if (url === '/models/m-1/download') return Promise.resolve({ download_url: 'https://oss/signed' })
      return Promise.resolve({})
    })
    const readyRow = wrapper.findAll('.el-table__row')[0]
    const downloadBtn = readyRow.findAll('button').find((b: any) => b.text().includes('下载'))
    await downloadBtn!.trigger('click')
    await flushPromises()
    expect(openSpy).toHaveBeenCalledWith('https://oss/signed', '_blank')

    // 切换为本地路径响应
    openSpy.mockClear()
    apiGet.mockImplementation((url: string) => {
      if (url === '/models') return Promise.resolve({ models: MODELS, total: 2 })
      if (url === '/models/m-1/download') return Promise.resolve({ local_path: '/data/models/m-1/best.pt' })
      return Promise.resolve({})
    })
    await downloadBtn!.trigger('click')
    await flushPromises()
    expect(openSpy).not.toHaveBeenCalled()
    expect(infoSpy).toHaveBeenCalled()
  })

  it('筛选无结果展示清除筛选，完全为空引导去训练任务', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/models' ? Promise.resolve({ models: [], total: 0 }) : Promise.resolve({})
    )
    const { wrapper } = await mountPage(ModelRegistry)
    // 默认 ready 筛选下为空 → 不算"有筛选"，显示引导空态
    expect(wrapper.text()).toContain('暂无注册模型')
    expect(wrapper.text()).toContain('查看训练任务')
  })

  it('加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('后端 502'))
    const { wrapper } = await mountPage(ModelRegistry)
    expect(wrapper.text()).toContain('模型加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/models' ? Promise.resolve({ models: MODELS, total: 2 }) : Promise.resolve({})
    )
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('iris-rf')
  })
})
