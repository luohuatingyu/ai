import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import NewSession from '../../workbench/NewSession.vue'

const { apiGet, apiPost } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/datasets') return Promise.resolve({ datasets: [{ id: 'ds-1', name: '销量数据', rows: 1200 }] })
    if (url === '/clusters/workers/summary')
      return Promise.resolve({ details: [{ worker_id: 'w1', device: 'gpu', gpu_name: 'A100', state: 'idle' }] })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
})

describe('NewSession', () => {
  // 说明：el-form 必填校验在 happy-dom 下不生效，空名拦截由
  // tests/e2e/form-validation.spec.ts 在真实浏览器中覆盖。

  it('配置摘要随表单实时更新', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(NewSession)

    // 名称同步到摘要
    await wrapper.find('input').setValue('我的实验')
    expect(wrapper.text()).toContain('我的实验')
    // 默认摘要
    expect(wrapper.text()).toContain('未关联')
    expect(wrapper.text()).toContain('未绑定')
  })

  it('内核选择卡片切换摘要状态', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(NewSession)
    const bashCard = wrapper.findAll('.kernel-option').find((c) => c.text().includes('Bash'))
    await bashCard!.trigger('click')
    const summary = wrapper.find('.summary-list').text()
    expect(summary).toContain('Bash')
  })

  it('勾选计算槽后摘要展示绑定数量', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(NewSession)
    const checkbox = wrapper.find('.worker-checkbox')
    await checkbox.find('input').setValue(true)
    await flushPromises()
    expect(wrapper.find('.summary-list').text()).toContain('1 个计算槽')
  })

  it('环境配置 JSON 非法时阻止提交并提示', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(NewSession)
    await wrapper.find('input').setValue('实验A')
    const textarea = wrapper.find('textarea')
    await textarea.setValue('{invalid json')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('创建并打开'))
    await submitBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('环境配置必须是合法的 JSON 对象')
    expect(apiPost).not.toHaveBeenCalled()
  })

  it('合法提交：解析 metadata 并跳转会话详情', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ session_id: 's-new-1' })
    const { wrapper, router } = await mountPage(NewSession)

    await wrapper.find('input').setValue('实验A')
    await wrapper.find('textarea').setValue('{"env":{"BATCH":"32"}}')
    const submitBtn = wrapper.findAll('button').find((b) => b.text().includes('创建并打开'))
    await submitBtn!.trigger('click')
    await flushPromises()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/workbench/sessions')
    expect(payload.name).toBe('实验A')
    expect(payload.metadata).toEqual({ env: { BATCH: '32' } })
    expect(payload.dataset_id).toBeNull() // 未选择数据集传 null
    expect(router.currentRoute.value.path).toBe('/workbench/session/s-new-1')
  })
})
