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
            h('span', { class: 'vf-node-stub', 'data-id': n.id }, n.label)
          ),
        ])
    },
  }),
}))
vi.mock('@vue-flow/background', () => ({ Background: { name: 'Background', render: () => null } }))
vi.mock('@vue-flow/controls', () => ({ Controls: { name: 'Controls', render: () => null } }))
vi.mock('@vue-flow/minimap', () => ({ MiniMap: { name: 'MiniMap', render: () => null } }))

import PipelineDetail from '../../pipelines/PipelineDetail.vue'

const { apiGet, apiPut, apiPost } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(),
  apiPost: vi.fn(),
}))
vi.mock('@/api', () => ({
  api: { get: apiGet, post: apiPost, put: apiPut, delete: vi.fn(), upload: vi.fn() },
}))

const PIPELINE = {
  id: 'p-1',
  name: '销量预测全流程',
  description: '拉数→训练→部署',
  schedule: '0 2 * * *',
  status: 'active',
  created_at: 1700000000,
  steps: [
    { id: 'step-pull', name: '拉取数据', type: 'dataset_pull', depends_on: [], config: { dataset_id: 'ds-1' }, timeout: 600, continue_on_failure: false, resource_hint: {}, retry_policy: {} },
    { id: 'step-train', name: '模型训练', type: 'training', depends_on: ['step-pull'], config: { epochs: 30 }, timeout: 3600, continue_on_failure: false, resource_hint: { require_gpu: true }, retry_policy: { max_retries: 2 } },
  ],
}

const routes = [{ path: '/pipelines/detail/:id', component: PipelineDetail }]

beforeEach(() => {
  apiGet.mockReset()
  apiPut.mockReset()
  apiPost.mockReset()
})

describe('PipelineDetail', () => {
  it('渲染概览与依赖图节点', async () => {
    apiGet.mockResolvedValue(PIPELINE)
    const { wrapper } = await mountPage(PipelineDetail, { routes, path: '/pipelines/detail/p-1' })
    const text = wrapper.text()
    expect(text).toContain('销量预测全流程')
    expect(text).toContain('已激活')
    expect(text).toContain('0 2 * * *')
    // 画布节点 + 步骤清单
    expect(wrapper.findAll('.vf-node-stub')).toHaveLength(2)
    expect(wrapper.findAll('.step-item')).toHaveLength(2)
  })

  it('点击节点打开配置抽屉并展示高级字段', async () => {
    apiGet.mockResolvedValue(PIPELINE)
    const { wrapper } = await mountPage(PipelineDetail, { routes, path: '/pipelines/detail/p-1' })

    const vf = wrapper.findComponent({ name: 'VueFlow' })
    vf.vm.$emit('node-click', { node: { id: 'step-train' } })
    await flushPromises()

    expect(wrapper.text()).toContain('步骤配置 · 模型训练')
    const drawerText = document.body.textContent || wrapper.text()
    expect(drawerText).toContain('30') // config.epochs
    expect(drawerText).toContain('require_gpu') // resource_hint
    expect(drawerText).toContain('max_retries') // retry_policy
  })

  it('已激活状态显示暂停/运行，隐藏激活', async () => {
    apiGet.mockResolvedValue(PIPELINE)
    const { wrapper } = await mountPage(PipelineDetail, { routes, path: '/pipelines/detail/p-1' })
    const buttons = wrapper.findAll('button').map((b: any) => b.text())
    expect(buttons.join()).toContain('暂停调度')
    expect(buttons.join()).toContain('立即运行')
    expect(buttons.join()).not.toContain('激活')
  })

  it('暂停/激活调用状态更新接口', async () => {
    apiGet.mockResolvedValue(PIPELINE)
    apiPut.mockResolvedValue({})
    const { wrapper } = await mountPage(PipelineDetail, { routes, path: '/pipelines/detail/p-1' })

    const pauseBtn = wrapper.findAll('button').find((b: any) => b.text().includes('暂停调度'))
    await pauseBtn!.trigger('click')
    await flushPromises()
    expect(apiPut).toHaveBeenCalledWith('/pipelines/p-1', { status: 'paused' })
  })

  it('立即运行跳转执行详情', async () => {
    apiGet.mockResolvedValue(PIPELINE)
    apiPost.mockResolvedValue({ run_id: 'run-9' })
    const { wrapper, router } = await mountPage(PipelineDetail, { routes, path: '/pipelines/detail/p-1' })

    const runBtn = wrapper.findAll('button').find((b: any) => b.text().includes('立即运行'))
    await runBtn!.trigger('click')
    await flushPromises()
    expect(apiPost).toHaveBeenCalledWith('/pipelines/p-1/run', { trigger: 'manual', parameters: {} })
    expect(router.currentRoute.value.path).toBe('/pipelines/run/p-1/run-9')
  })

  it('加载失败展示可重试错误态', async () => {
    apiGet.mockRejectedValue(new Error('不存在'))
    const { wrapper } = await mountPage(PipelineDetail, { routes, path: '/pipelines/detail/p-1' })
    expect(wrapper.text()).toContain('Pipeline 加载失败')

    apiGet.mockResolvedValue(PIPELINE)
    const retryBtn = wrapper.findAll('button').find((b: any) => b.text().includes('重试'))
    await retryBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('销量预测全流程')
  })
})
