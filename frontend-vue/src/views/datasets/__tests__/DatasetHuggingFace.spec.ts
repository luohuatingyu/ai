import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import DatasetHuggingFace from '../../datasets/DatasetHuggingFace.vue'

const { apiPost } = vi.hoisted(() => ({ apiPost: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: vi.fn(), post: apiPost, put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

beforeEach(() => {
  apiPost.mockReset()
})

/** 填写合法表单并提交 */
async function submitValidForm(wrapper: any) {
  const inputs = wrapper.findAll('input')
  await inputs[0].setValue('imdb') // 仓库 ID
  const submitBtn = wrapper.findAll('button').find((b: any) => b.text().includes('导入数据集'))
  await submitBtn!.trigger('click')
  await flushPromises()
}

describe('DatasetHuggingFace', () => {
  // 说明：el-form 校验拦截在 happy-dom 环境下不生效（Element Plus 2.14 限制），
  // 空表单提交拦截由 tests/e2e/form-validation.spec.ts 在真实浏览器中覆盖。

  it('点击快捷入口自动填充仓库 ID', async () => {
    const { wrapper } = await mountPage(DatasetHuggingFace)
    const chip = wrapper.findAll('.preset-chip').find((c) => c.text() === 'imdb')
    await chip!.trigger('click')
    const inputs = wrapper.findAll('input')
    expect((inputs[0].element as HTMLInputElement).value).toBe('imdb')
  })

  it('合法提交调用导入接口，子集仅在有值时传递', async () => {
    apiPost.mockResolvedValue({ id: 'ds-hf-1' })
    const { wrapper } = await mountPage(DatasetHuggingFace)
    await submitValidForm(wrapper)

    // 第一次调用是导入；引用模式下随后还有一次物化调用
    const hubCalls = apiPost.mock.calls.filter((c) => c[0] === '/datasets/hub')
    expect(hubCalls).toHaveLength(1)
    const [, payload] = hubCalls[0]
    expect(payload.repo_id).toBe('imdb')
    expect(payload.split).toBe('train')
    expect(payload.storage_mode).toBe('remote')
    expect(payload).not.toHaveProperty('subset') // 子集为空不传
  })

  it('引用模式下导入成功后立即物化，展示结果面板', async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === '/datasets/hub') return Promise.resolve({ id: 'ds-hf-1' })
      if (url === '/datasets/ds-hf-1/materialize') return Promise.resolve({})
      return Promise.resolve({})
    })
    const { wrapper } = await mountPage(DatasetHuggingFace)
    await submitValidForm(wrapper)

    expect(wrapper.text()).toContain('导入成功')
    expect(wrapper.text()).toContain('ds-hf-1')
    expect(wrapper.text()).toContain('数据已拉取到本地')
  })

  it('物化失败不影响导入结果（降级为自动拉取提示）', async () => {
    apiPost.mockImplementation((url: string) => {
      if (url === '/datasets/hub') return Promise.resolve({ id: 'ds-hf-2' })
      return Promise.reject(new Error('网络抖动'))
    })
    const { wrapper } = await mountPage(DatasetHuggingFace)
    await submitValidForm(wrapper)

    expect(wrapper.text()).toContain('导入成功')
    expect(wrapper.text()).toContain('数据将在首次使用时自动拉取')
  })

  it('导入接口失败时停留当前页并提示错误', async () => {
    apiPost.mockRejectedValue(new Error('仓库不存在'))
    const { wrapper, router } = await mountPage(DatasetHuggingFace)
    await submitValidForm(wrapper)

    expect(wrapper.text()).not.toContain('导入成功')
    expect(router.currentRoute.value.path).not.toBe('/datasets/list')
  })
})
