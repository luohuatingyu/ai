import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import DatasetImport from '../../datasets/DatasetImport.vue'

const { apiGet, apiPost, apiUpload } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiUpload: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: vi.fn(), upload: apiUpload },
}))

const SAMPLES = [
  { name: 'iris.csv', description: '鸢尾花分类数据' },
  { name: 'sales.csv', description: '' },
]
const HISTORY = [
  { dataset_id: 'ds-1', filename: 'iris.csv', format: 'csv', rows: 150, status: 'success', imported_at: 1700000000 },
  { dataset_id: 'ds-2', filename: 'bad.csv', format: 'csv', rows: 0, status: 'failed', imported_at: 1700001000 },
]

/** 配置默认接口响应 */
function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/samples') return Promise.resolve(SAMPLES)
    if (url === '/datasets/import-history') return Promise.resolve({ history: HISTORY })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiUpload.mockReset()
})

describe('DatasetImport', () => {
  it('未选择文件时上传按钮禁用', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(DatasetImport)
    const uploadBtn = wrapper.findAll('button').find((b) => b.text().includes('上传并导入'))
    expect(uploadBtn!.attributes('disabled')).toBeDefined()
  })

  it('选择文件后提交调用上传接口并携带描述与标签', async () => {
    mockApiSuccess()
    apiUpload.mockResolvedValue({ datasets: [{ id: 'ds-9' }] })
    const { wrapper } = await mountPage(DatasetImport)

    // 直接调用 el-upload 的 on-change prop（文件选中回调）
    const fakeFile = new File(['a,b'], 'data.csv', { type: 'text/csv' })
    const upload = wrapper.findComponent({ name: 'ElUpload' })
    ;(upload.props('onChange') as any)?.({ raw: fakeFile, name: 'data.csv' })
    await flushPromises()

    const uploadBtn = wrapper.findAll('button').find((b: any) => b.text().includes('上传并导入'))
    expect(uploadBtn!.attributes('disabled')).toBeUndefined()

    await wrapper.find('textarea').setValue('测试描述')
    await uploadBtn!.trigger('click')
    await flushPromises()

    expect(apiUpload).toHaveBeenCalledWith(
      '/datasets/import',
      fakeFile,
      undefined,
      { description: '测试描述', tags: [] }
    )
  })

  it('上传失败时保留错误提示且不清空表单', async () => {
    mockApiSuccess()
    apiUpload.mockRejectedValue(new Error('文件解析失败'))
    const { wrapper } = await mountPage(DatasetImport)

    const fakeFile = new File(['a'], 'data.csv', { type: 'text/csv' })
    const upload = wrapper.findComponent({ name: 'ElUpload' })
    ;(upload.props('onChange') as any)?.({ raw: fakeFile, name: 'data.csv' })
    await flushPromises()

    const uploadBtn = wrapper.findAll('button').find((b: any) => b.text().includes('上传并导入'))
    await uploadBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('文件解析失败')
    expect(wrapper.text()).toContain('上传并导入') // 按钮仍在，可重试
  })

  it('渲染示例数据并可一键导入', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ id: 'ds-sample' })
    const { wrapper } = await mountPage(DatasetImport)

    expect(wrapper.text()).toContain('iris.csv')
    expect(wrapper.text()).toContain('鸢尾花分类数据')

    const importBtn = wrapper.findAll('button').find((b) => b.text() === '导入')
    await importBtn!.trigger('click')
    await flushPromises()
    expect(apiPost).toHaveBeenCalledWith('/datasets/sample/iris.csv')
  })

  it('渲染导入历史：状态标签与行数格式化', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(DatasetImport)
    expect(wrapper.text()).toContain('ds-1')
    expect(wrapper.text()).toContain('成功')
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.text()).toContain('150')
  })

  it('示例加载失败时展示错误态并可重试', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/samples' ? Promise.reject(new Error('后端 502')) : Promise.resolve({ history: [] })
    )
    const { wrapper } = await mountPage(DatasetImport)
    expect(wrapper.text()).toContain('示例数据加载失败')

    apiGet.mockImplementation((url: string) =>
      url === '/samples' ? Promise.resolve(SAMPLES) : Promise.resolve({ history: [] })
    )
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('iris.csv')
  })
})
