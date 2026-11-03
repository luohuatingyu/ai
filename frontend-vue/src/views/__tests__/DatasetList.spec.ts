import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { ElMessageBox } from 'element-plus'
import { createPinia } from 'pinia'
import { useAppStore } from '@/stores/app'
import { mountPage } from '@/test/mountPage'
import DatasetList from '../datasets/DatasetList.vue'

// API 客户端打桩
const { apiGet, apiDelete } = vi.hoisted(() => ({ apiGet: vi.fn(), apiDelete: vi.fn() }))
vi.mock('@/api', () => ({
  api: {
    get: apiGet,
    post: vi.fn(),
    put: vi.fn(),
    delete: apiDelete,
    upload: vi.fn(),
  },
}))

const DATASETS = [
  { id: 'ds-1', name: '销量数据', source: 'local', rows: 1200, columns: ['date', 'sales'], tags: ['销售'], created_at: 1700000000 },
  { id: 'ds-2', name: 'IMDB 评论', source: 'huggingface', rows: 25000, columns: ['text', 'label'], tags: [], created_at: 1700005000 },
]

/** 配置列表与标签接口的默认响应 */
function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/datasets') return Promise.resolve({ datasets: DATASETS, total: 2 })
    if (url === '/datasets/tags') return Promise.resolve({ tags: ['销售', 'NLP'] })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiDelete.mockReset()
  vi.clearAllMocks()
})

describe('DatasetList', () => {
  it('渲染数据集行：名称、来源中文标签与规模', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(DatasetList)
    const text = wrapper.text()
    expect(text).toContain('销量数据')
    expect(text).toContain('IMDB 评论')
    expect(text).toContain('本地上传')
    expect(text).toContain('HuggingFace')
    expect(text).toContain('1,200') // 千分位行数
    expect(text).toContain('共 2 个数据集')
  })

  it('搜索时携带筛选参数并回到第一页', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(DatasetList)
    apiGet.mockClear()

    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('销量')
    const searchBtn = wrapper.findAll('button').find((b) => b.text() === '搜索')
    await searchBtn!.trigger('click')
    await flushPromises()

    const [url, params] = apiGet.mock.calls[0]
    expect(url).toBe('/datasets')
    expect(params.search).toBe('销量')
    expect(params.offset).toBe(0) // 搜索回到第一页
  })

  it('完全无数据时展示引导空态', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/datasets' ? Promise.resolve({ datasets: [], total: 0 }) : Promise.resolve({ tags: [] })
    )
    const { wrapper } = await mountPage(DatasetList)
    expect(wrapper.text()).toContain('暂无数据集')
    expect(wrapper.text()).toContain('导入数据集')
  })

  it('筛选无结果时展示"没有匹配"空态并提供清除筛选', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/datasets' ? Promise.resolve({ datasets: [], total: 0 }) : Promise.resolve({ tags: [] })
    )
    const { wrapper } = await mountPage(DatasetList)
    const input = wrapper.find('input[placeholder*="搜索"]')
    await input.setValue('不存在的数据集')
    const searchBtn = wrapper.findAll('button').find((b) => b.text() === '搜索')
    await searchBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('没有匹配的数据集')
    expect(wrapper.text()).toContain('清除筛选')
  })

  it('列表加载失败时展示错误态并支持重试', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/datasets' ? Promise.reject(new Error('后端 502')) : Promise.resolve({ tags: [] })
    )
    const { wrapper } = await mountPage(DatasetList)
    expect(wrapper.text()).toContain('数据集加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/datasets' ? Promise.resolve({ datasets: DATASETS, total: 2 }) : Promise.resolve({ tags: [] })
    )
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('销量数据')
  })

  it('删除经二次确认后调用删除接口并刷新列表', async () => {
    mockApiSuccess()
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as any)
    apiDelete.mockResolvedValue({})
    // 挂载前建立 store spy（删除成功后调用 success）
    const pinia = createPinia()
    const successSpy = vi.spyOn(useAppStore(pinia), 'success')
    const { wrapper } = await mountPage(DatasetList, { pinia })

    const dropdown = wrapper.findComponent({ name: 'ElDropdown' })
    expect(dropdown.exists()).toBe(true)
    dropdown.vm.$emit('command', 'delete', DATASETS[0])
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(confirmSpy.mock.calls[0][0]).toContain('销量数据') // 确认文案包含数据集名称
    expect(apiDelete).toHaveBeenCalledWith('/datasets/ds-1')
    expect(successSpy).toHaveBeenCalled()
  })

  it('取消删除时不调用删除接口', async () => {
    mockApiSuccess()
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    const { wrapper } = await mountPage(DatasetList)

    const dropdown = wrapper.findComponent({ name: 'ElDropdown' })
    dropdown.vm.$emit('command', 'delete', DATASETS[0])
    await flushPromises()

    expect(apiDelete).not.toHaveBeenCalled()
  })
})
