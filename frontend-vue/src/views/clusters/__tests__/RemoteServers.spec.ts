import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountPage } from '@/test/mountPage'
import RemoteServers from '../../clusters/RemoteServers.vue'

const { apiGet, apiPost, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: apiDelete, upload: vi.fn() },
}))

const SERVERS = [
  { machine_id: 'gpu-node-01', host: '192.168.1.10', port: 22, username: 'root', mode: 'gpu', cpu_slots: 4, deployed: true, status: 'online' },
  { machine_id: 'cpu-node-01', host: '192.168.1.20', port: 2222, username: '', mode: 'cpu', cpu_slots: 2, deployed: false, status: 'not_deployed' },
]

const CREDENTIALS = [
  { id: 'cred-1', name: 'prod-cluster', username: 'root', auth_method: 'password', is_default: true },
  { id: 'cred-2', name: 'key-auth', username: 'ubuntu', auth_method: 'key', is_default: false },
]

function mockApiSuccess() {
  apiGet.mockImplementation((url: string) => {
    if (url === '/remote-servers') return Promise.resolve({ servers: SERVERS })
    if (url === '/remote-ssh-credentials') return Promise.resolve({ credentials: CREDENTIALS })
    if (url === '/remote-operations/op-1') return Promise.resolve({ status: 'running', output: 'step 1 done' })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  apiGet.mockReset()
  apiPost.mockReset()
  apiDelete.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RemoteServers', () => {
  it('渲染服务器行：主机、模式、部署状态与生命周期', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(RemoteServers)
    const text = wrapper.text()
    expect(text).toContain('gpu-node-01')
    expect(text).toContain('192.168.1.10:22')
    expect(text).toContain('GPU')
    expect(text).toContain('在线')
    expect(text).toContain('已部署')
    expect(text).toContain('未部署')
    expect(text).toContain('同步代码') // 已部署服务器
    expect(text).toContain('部署') // 未部署服务器
  })

  it('登记服务器提交 POST 载荷（username 仅在填写时提交）', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({})
    const { wrapper } = await mountPage(RemoteServers)

    const createBtn = wrapper.findAll('button').find((b: any) => b.text().includes('登记服务器'))
    await createBtn!.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.el-dialog')
    const inputs = dialog.findAll('input')
    await inputs[0].setValue('gpu-node-02') // 机器 ID
    await inputs[1].setValue('192.168.1.30') // SSH 主机
    // 选择凭证（对话框中的 ElSelect）
    const select = dialog.findComponent({ name: 'ElSelect' })
    select.vm.$emit('update:modelValue', 'cred-1')
    select.vm.$emit('change', 'cred-1')
    await flushPromises()

    const registerBtn = wrapper.findAll('button').find((b: any) => b.text() === '登记')
    await registerBtn!.trigger('click')
    await flushPromises()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/remote-servers')
    expect(payload.machine_id).toBe('gpu-node-02')
    expect(payload.host).toBe('192.168.1.30')
    expect(payload.port).toBe(22)
    expect(payload.ssh_credential_id).toBe('cred-1')
    expect(payload.mode).toBe('gpu')
    expect(payload).not.toHaveProperty('username') // 未填写不提交
  })

  // 说明：机器 ID 为空/非法时的表单校验拦截依赖 Element Plus 校验，
  // happy-dom 下不生效，由 tests/e2e/form-validation.spec.ts 在真实浏览器覆盖。

  it('新建凭证按认证方式组装敏感字段', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ credentials: CREDENTIALS })
    const { wrapper } = await mountPage(RemoteServers)

    // 打开凭证管理 → 新建凭证（嵌套对话框 append-to-body，渲染在 document.body）
    const credBtn = wrapper.findAll('button').find((b: any) => b.text().includes('SSH 凭证'))
    await credBtn!.trigger('click')
    await flushPromises()
    const newBtn = wrapper.findAll('button').find((b: any) => b.text().includes('新建凭证'))
    await newBtn!.trigger('click')
    await flushPromises()

    // 从 body 中找到最后一个对话框（新建凭证表单）
    const dialogs = document.body.querySelectorAll('.el-dialog')
    const credDialog = dialogs[dialogs.length - 1]
    expect(credDialog).toBeTruthy()

    const setValue = (el: Element, value: string) => {
      const input = el as HTMLInputElement
      input.value = value
      input.dispatchEvent(new Event('input', { bubbles: true }))
    }
    const inputs = credDialog.querySelectorAll('input')
    setValue(inputs[0], 'new-cred') // 名称
    setValue(inputs[1], 'deploy') // 用户名
    const passwordInput = credDialog.querySelector('input[type="password"]')!
    setValue(passwordInput, 'secret-pass')

    const saveBtn = Array.from(credDialog.querySelectorAll('button')).find((b) => b.textContent?.includes('保存'))
    saveBtn!.click()
    await flushPromises()

    const [url, payload] = apiPost.mock.calls[0]
    expect(url).toBe('/remote-ssh-credentials')
    expect(payload.name).toBe('new-cred')
    expect(payload.auth_method).toBe('password')
    expect(payload.password).toBe('secret-pass')
    expect(payload).not.toHaveProperty('private_key')
  })

  it('删除凭证调用 DELETE 并更新列表', async () => {
    mockApiSuccess()
    apiDelete.mockResolvedValue({ credentials: [] })
    const { wrapper } = await mountPage(RemoteServers)

    const credBtn = wrapper.findAll('button').find((b: any) => b.text().includes('SSH 凭证'))
    await credBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('prod-cluster')

    const popconfirm = wrapper.findAllComponents({ name: 'ElPopconfirm' })[0]
    popconfirm.vm.$emit('confirm')
    await flushPromises()

    expect(apiDelete).toHaveBeenCalledWith('/remote-ssh-credentials/cred-1')
  })

  it('下发远程操作后轮询状态直到完成并刷新列表', async () => {
    mockApiSuccess()
    apiPost.mockResolvedValue({ operation_id: 'op-1', status: 'queued' })
    const { wrapper } = await mountPage(RemoteServers)

    // 已部署服务器点击「同步代码」
    const firstRow = wrapper.findAll('.el-table__row')[0]
    const deployBtn = firstRow.findAll('button').find((b: any) => b.text().includes('同步代码'))
    await deployBtn!.trigger('click')
    await flushPromises()

    expect(apiPost).toHaveBeenCalledWith('/remote-servers/gpu-node-01/deploy')
    // 操作对话框打开，显示执行状态
    expect(wrapper.text()).toContain('远程操作：同步代码')

    // 推进轮询定时器：第一次轮询 → running
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(apiGet).toHaveBeenCalledWith('/remote-operations/op-1')
    expect(wrapper.text()).toContain('step 1 done')

    // 第二次轮询 → completed，停止轮询并刷新
    apiGet.mockImplementation((url: string) => {
      if (url === '/remote-servers') return Promise.resolve({ servers: SERVERS })
      if (url === '/remote-operations/op-1') return Promise.resolve({ status: 'completed', output: 'all done' })
      return Promise.resolve({})
    })
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(wrapper.text()).toContain('all done')

    // 完成分支在本次轮询内执行 stopPolling：此后不应再有轮询调用
    const pollCalls = apiGet.mock.calls.filter((c) => c[0] === '/remote-operations/op-1').length
    await vi.advanceTimersByTimeAsync(6000)
    await flushPromises()
    const pollCallsAfter = apiGet.mock.calls.filter((c) => c[0] === '/remote-operations/op-1').length
    expect(pollCallsAfter).toBe(pollCalls)
  })
})
