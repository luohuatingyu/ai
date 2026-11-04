import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { defineComponent, h } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia } from 'pinia'

// Vue Flow 打桩：以渲染函数呈现节点（避免运行时模板编译），支持 node-click 事件透传
vi.mock('@vue-flow/core', () => ({
  VueFlow: defineComponent({
    name: 'VueFlow',
    props: ['nodes', 'edges', 'fitViewOnInit', 'nodesConnectable', 'elementsSelectable', 'minZoom', 'maxZoom'],
    emits: ['node-click'],
    setup(props) {
      return () =>
        h('div', { class: 'vf-stub' }, [
          (props.nodes || []).map((n: any) =>
            h('span', { class: 'vf-node-stub', 'data-id': n.id }, n.label)
          ),
        ])
    },
  }),
}))
vi.mock('@vue-flow/background', () => ({ Background: { name: 'Background', render: () => null } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { name: 'Controls', render: () => null } }))
vi.mock('@vue-flow/minimap', () => ({ MiniMap: { name: 'MiniMap', render: () => null } }))

import PipelineCreate from '../../pipelines/PipelineCreate.vue'

const { apiGet, apiPost, apiPut } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: apiPut, delete: vi.fn(), upload: vi.fn() },
}))

const EXISTING_PIPELINE = {
  id: 'p-1',
  name: '销量预测全流程',
  description: '拉数→训练→部署',
  schedule: '0 2 * * *',
  steps: [
    {
      id: 'step-pull',
      name: '拉取数据',
      type: 'dataset_pull',
      config: { dataset_id: 'ds-1' },
      depends_on: [],
      // 高级字段：编辑保存后必须原样保留（回归保护）
      resource_hint: { require_gpu: false },
      retry_policy: { max_retries: 2 },
      timeout: 7200,
      continue_on_failure: true,
    },
    {
      id: 'step-train',
      name: '模型训练',
      type: 'training',
      config: { dataset_id: 'ds-1', epochs: 30 },
      depends_on: ['step-pull'],
      resource_hint: { require_gpu: true },
      retry_policy: {},
      timeout: 3600,
      continue_on_failure: false,
    },
  ],
}

function mountCreate(query = '') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/pipelines/create', component: PipelineCreate }],
  })
  router.push(`/pipelines/create${query}`)
  return router.isReady().then(() =>
    mount(PipelineCreate, { global: { plugins: [ElementPlus, router, createPinia()] } })
  )
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
  apiPut.mockReset()
})

describe('PipelineCreate', () => {
  it('创建模式：添加步骤后画布出现节点', async () => {
    const wrapper = await mountCreate()
    await flushPromises()

    const addBtn = wrapper.findAll('button').find((b: any) => b.text().includes('添加步骤'))
    await addBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.vf-node-stub')).toHaveLength(1)
    // 画布节点默认标签为步骤 ID
    expect(wrapper.find('.vf-node-stub').text()).toBeTruthy()
  })

  it('编辑模式：加载后画布呈现全部节点与依赖边', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/pipelines/p-1' ? Promise.resolve(EXISTING_PIPELINE) : Promise.resolve({})
    )
    const wrapper = await mountCreate('?id=p-1')
    await flushPromises()
    await flushPromises()

    expect(wrapper.findAll('.vf-node-stub')).toHaveLength(2)
    // 名称与调度预填
    const inputs = wrapper.findAll('input')
    expect((inputs[0].element as HTMLInputElement).value).toBe('销量预测全流程')
  })

  it('编辑保存：高级字段（resource_hint/retry_policy/timeout/continue_on_failure）完整保留', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/pipelines/p-1' ? Promise.resolve(EXISTING_PIPELINE) : Promise.resolve({})
    )
    apiPut.mockResolvedValue({})
    const wrapper = await mountCreate('?id=p-1')
    await flushPromises()
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find((b: any) => b.text().includes('保存修改'))
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(apiPut).toHaveBeenCalled()
    const [url, payload] = apiPut.mock.calls[0]
    expect(url).toBe('/pipelines/p-1')
    const steps = payload.steps as any[]
    expect(steps).toHaveLength(2)

    const pull = steps.find((s) => s.id === 'step-pull')
    expect(pull.resource_hint).toEqual({ require_gpu: false }) // 原样保留，不再被 {} 覆盖
    expect(pull.retry_policy).toEqual({ max_retries: 2 })
    expect(pull.timeout).toBe(7200) // 不再被固定 3600 覆盖
    expect(pull.continue_on_failure).toBe(true) // 不再被固定 false 覆盖
    expect(pull.config).toEqual({ dataset_id: 'ds-1' })
    expect(pull.depends_on).toEqual([])

    const train = steps.find((s) => s.id === 'step-train')
    expect(train.resource_hint).toEqual({ require_gpu: true })
    expect(train.depends_on).toEqual(['step-pull'])
  })

  it('循环依赖时保存被阻断并提示', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/pipelines/p-1' ? Promise.resolve(EXISTING_PIPELINE) : Promise.resolve({})
    )
    const wrapper = await mountCreate('?id=p-1')
    await flushPromises()
    await flushPromises()

    // 通过属性面板构造环：选中 step-pull 并加入对 step-train 的依赖
    const vf = wrapper.findComponent({ name: 'VueFlow' })
    vf.vm.$emit('node-click', { node: { id: 'step-pull' } })
    await flushPromises()

    // 属性面板中的依赖 select（第二个 ElSelect：类型之后）
    const panelSelects = wrapper.findAllComponents({ name: 'ElSelect' })
    const depSelect = panelSelects[panelSelects.length - 1]
    depSelect.vm.$emit('update:modelValue', ['step-train'])
    await flushPromises()

    // 页面出现循环依赖错误告警
    expect(wrapper.text()).toContain('存在循环依赖')

    const saveBtn = wrapper.findAll('button').find((b: any) => b.text().includes('保存修改'))
    await saveBtn!.trigger('click')
    await flushPromises()

    // 循环依赖（pull→train→pull）应阻断保存
    expect(apiPut).not.toHaveBeenCalled()
  })

  it('配置 JSON 非法时保存被阻断', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/pipelines/p-1' ? Promise.resolve(EXISTING_PIPELINE) : Promise.resolve({})
    )
    const wrapper = await mountCreate('?id=p-1')
    await flushPromises()
    await flushPromises()

    // 选中第一个节点，破坏其配置 JSON
    const vf = wrapper.findComponent({ name: 'VueFlow' })
    vf.vm.$emit('node-click', { node: { id: 'step-pull' } })
    await flushPromises()
    const textareas = wrapper.findAll('textarea')
    await textareas[0].setValue('{invalid}')

    const saveBtn = wrapper.findAll('button').find((b: any) => b.text().includes('保存修改'))
    await saveBtn!.trigger('click')
    await flushPromises()

    // 内联实时校验提示 + 提交被阻断
    expect(wrapper.text()).toContain('运行配置必须是合法 JSON 对象')
    expect(apiPut).not.toHaveBeenCalled()
  })

  it('移除步骤后其他步骤对它的依赖被清理', async () => {
    apiGet.mockImplementation((url: string) =>
      url === '/pipelines/p-1' ? Promise.resolve(EXISTING_PIPELINE) : Promise.resolve({})
    )
    apiPut.mockResolvedValue({})
    const wrapper = await mountCreate('?id=p-1')
    await flushPromises()
    await flushPromises()

    // 选中 step-pull 并移除
    const vf = wrapper.findComponent({ name: 'VueFlow' })
    vf.vm.$emit('node-click', { node: { id: 'step-pull' } })
    await flushPromises()
    const removeBtn = wrapper.findAll('button').find((b: any) => b.text().includes('移除步骤'))
    await removeBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.vf-node-stub')).toHaveLength(1) // 只剩 train

    // 保存后 train 的依赖应已清理
    const saveBtn = wrapper.findAll('button').find((b: any) => b.text().includes('保存修改'))
    await saveBtn!.trigger('click')
    await flushPromises()
    const [, payload] = apiPut.mock.calls[0]
    expect((payload.steps as any[])[0].depends_on).toEqual([])
  })

  it('加载失败展示错误提示且不渲染画布', async () => {
    apiGet.mockRejectedValue(new Error('Pipeline 不存在'))
    const wrapper = await mountCreate('?id=p-1')
    await flushPromises()
    expect(wrapper.text()).toContain('Pipeline 加载失败')
    expect(wrapper.find('.vf-stub').exists()).toBe(false)
  })
})
