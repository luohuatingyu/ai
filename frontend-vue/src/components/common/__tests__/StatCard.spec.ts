import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import StatCard from '../StatCard.vue'

describe('StatCard', () => {
  const mountOptions = { global: { plugins: [ElementPlus] } }

  it('渲染标签、数值与提示', () => {
    const wrapper = mount(StatCard, {
      ...mountOptions,
      props: { label: '训练任务', value: 12, hint: '全部任务' },
    })
    expect(wrapper.text()).toContain('训练任务')
    expect(wrapper.text()).toContain('12')
    expect(wrapper.text()).toContain('全部任务')
  })

  it('默认渲染为 div 且不可点击', () => {
    const wrapper = mount(StatCard, {
      ...mountOptions,
      props: { label: 'A', value: 1 },
    })
    expect(wrapper.element.tagName).toBe('DIV')
  })

  it('clickable 时渲染为 button 并发出 click 事件（键盘可达）', async () => {
    const wrapper = mount(StatCard, {
      ...mountOptions,
      props: { label: 'A', value: 1, clickable: true },
    })
    expect(wrapper.element.tagName).toBe('BUTTON')
    expect((wrapper.element as HTMLButtonElement).type).toBe('button')
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })
})
