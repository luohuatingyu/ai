import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import EmptyState from '../EmptyState.vue'

const mountOptions = {
  global: { plugins: [ElementPlus] },
}

describe('EmptyState', () => {
  it('空态：渲染标题与描述', () => {
    const wrapper = mount(EmptyState, {
      ...mountOptions,
      props: { title: '暂无数据集', description: '先导入一个数据集' },
    })
    expect(wrapper.text()).toContain('暂无数据集')
    expect(wrapper.text()).toContain('先导入一个数据集')
    // 非错误态不显示重试按钮
    expect(wrapper.text()).not.toContain('重试')
  })

  it('错误态：显示重试按钮并发出 retry 事件', async () => {
    const wrapper = mount(EmptyState, {
      ...mountOptions,
      props: { type: 'error', title: '加载失败', description: '网络错误', retryable: true },
    })
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    expect(retryBtn).toBeTruthy()
    await retryBtn!.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('默认插槽 actions 正常渲染', () => {
    const wrapper = mount(EmptyState, {
      ...mountOptions,
      props: { title: '暂无数据' },
      slots: { actions: '<button class="custom-action">去导入</button>' },
    })
    expect(wrapper.find('.custom-action').exists()).toBe(true)
  })
})
