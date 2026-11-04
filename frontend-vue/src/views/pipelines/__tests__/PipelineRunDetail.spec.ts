import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { mountPage } from '@/test/mountPage'

vi.mock('@vue-flow/core', () => ({
  VueFlow: defineComponent({
    name: 'VueFlow',
    props: ['nodes', 'edges'],
    emits: ['node-click'],
    setup(props) {
      return () =>
        h('div', { class: 'vf-stub' }, [
          (props.nodes || []).map((n: any) =>
            h('span', { class: 'vf-node-stub', 'data-id': n.id, 'data-status': n.status }, n.label)
          ),
        ])
    },
  }),
}))
vi.mock('@vue-flow/background', () => ({ Background: { name: 'Background', render: () => null } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { name: 'Controls', render: () => null } }))

import PipelineRunDetail from '../../pipelines/PipelineRunDetail.vue'

const { apiGet, apiPost } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: vi.fn(), delete: vi.fn(), upload: vi.fn() },
}))

const RUN = {
  id: 'run-1',
  pipeline_id: 'p-1',
  trigger: 'manual',
  status: 'completed',
  step_states: { 'step-pull': 'completed', 'step-train': 'completed' },
  error: null,
  started_at: 1700000000,
  finished_at: 1700000900,
  steps: [
    { id: 'sr-1', step_id: 'step-pull', step_name: '拉取数据', step_type: 'dataset_pull', status: 'completed', config: {}, worker_id: 'w1', retry_count: 0, started_at: 1700000000, finished_at: 1700000300, error: null, logs: ['pull done'], output_artifacts: {} },
    { id: 'sr-2', step_id: 'step-train', step_name: '模型训练', step_type: 'training', status: 'completed', config: {}, worker_id: 'w2', retry_count: 1, started_at: 1700000300, finished_at: 1700000900, error: null, logs: ['epoch 1', 'epoch 2'], output_artifacts: { best_pt: '/data/best.pt' } },
  ],
}

const PIPELINE_DEF = {
  id: 'p-1',
  steps: [
    { id: 'step-pull', name: '拉取数据', depends_on: [] },
    { id: 'step-train', name: '模型训练', depends_on: ['step-pull'] },
  ],
}

const routes = [{ path: '/pipelines/run/:pipelineId/:runId', component: PipelineRunDetail }]

function mockApiSuccess(overrides: Record<string, any> = {}) {
  apiGet.mockImplementation((url: string) => {
    if (url === '/pipelines/p-1/runs/run-1') return Promise.resolve(overrides.run ?? RUN)
    if (url === '/pipelines/p-1') return Promise.resolve(overrides.def ?? PIPELINE_DEF)
    return Promise.resolve({})
  })
}

beforeEach(() => {
  apiGet.mockReset()
  apiPost.mockReset()
})

describe('PipelineRunDetail', () => {
  it('渲染执行概要与步骤状态图（依赖来自 Pipeline 定义）', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })
    const text = wrapper.text()
    expect(text).toContain('run-1')
    expect(text).toContain('已完成')
    expect(text).toContain('手动')
    // 图节点来自 Pipeline 定义（含依赖边结构）
    expect(wrapper.findAll('.vf-node-stub')).toHaveLength(2)
    // 步骤清单
    expect(wrapper.findAll('.run-step-item')).toHaveLength(2)
  })

  it('点击节点打开步骤日志抽屉并展示日志与产物', async () => {
    mockApiSuccess()
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })

    const vf = wrapper.findComponent({ name: 'VueFlow' })
    vf.vm.$emit('node-click', { node: { id: 'step-train' } })
    await flushPromises()

    expect(wrapper.text()).toContain('步骤日志 · 模型训练')
    const drawerText = wrapper.text()
    expect(drawerText).toContain('epoch 1')
    expect(drawerText).toContain('/data/best.pt') // 输出产物
  })

  it('失败执行顶部展示错误告警且隐藏取消按钮', async () => {
    mockApiSuccess({ run: { ...RUN, status: 'failed', error: '步骤 model_train 失败：OOM' } })
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })
    expect(wrapper.text()).toContain('执行失败：步骤 model_train 失败：OOM')
    expect(wrapper.text()).not.toContain('取消执行')
  })

  it('运行中的执行显示取消按钮，取消后调用取消接口', async () => {
    mockApiSuccess({ run: { ...RUN, status: 'running', finished_at: null } })
    apiPost.mockResolvedValue({})
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })

    const cancelBtn = wrapper.findAll('button').find((b: any) => b.text().includes('取消执行'))
    await cancelBtn!.trigger('click')
    await flushPromises()
    expect(apiPost).toHaveBeenCalledWith('/pipelines/p-1/runs/run-1/cancel')
  })

  it('Pipeline 定义加载失败时降级为无依赖列表，不阻塞执行数据', async () => {
    apiGet.mockImplementation((url: string) => {
      if (url === '/pipelines/p-1/runs/run-1') return Promise.resolve(RUN)
      if (url === '/pipelines/p-1') return Promise.reject(new Error('定义不存在'))
      return Promise.resolve({})
    })
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })
    // 执行数据正常展示
    expect(wrapper.text()).toContain('run-1')
    expect(wrapper.findAll('.run-step-item')).toHaveLength(2)
  })

  it('执行详情加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('执行不存在'))
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })
    expect(wrapper.text()).toContain('执行详情加载失败')

    apiGet.mockImplementation((url: string) => {
      if (url === '/pipelines/p-1/runs/run-1') return Promise.resolve(RUN)
      return Promise.resolve({})
    })
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已完成')
  })

  it('图节点按执行状态携带状态 class，并渲染状态图例', async () => {
    mockApiSuccess({
      run: {
        ...RUN,
        status: 'running',
        steps: [
          { ...RUN.steps[0], status: 'completed' },
          { ...RUN.steps[1], status: 'running' },
        ],
      },
    })
    const { wrapper } = await mountPage(PipelineRunDetail, { routes, path: '/pipelines/run/p-1/run-1' })

    // 节点 class 编码执行状态（供 CSS 着色：完成绿 / 运行中蓝）
    const nodes = wrapper.findComponent({ name: 'VueFlow' }).props('nodes') as any[]
    expect(nodes[0].class).toContain('run-node--completed')
    expect(nodes[1].class).toContain('run-node--running')

    // 图例说明状态颜色含义
    expect(wrapper.find('.run-legend').exists()).toBe(true)
    expect(wrapper.find('.run-legend').text()).toContain('失败')
  })
})
