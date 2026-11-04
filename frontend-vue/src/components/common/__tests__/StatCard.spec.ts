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

  it('valueTone 渲染数值语义强调 class', () => {
    const danger = mount(StatCard, {
      ...mountOptions,
      props: { label: '失败', value: 3, valueTone: 'danger' },
    })
    expect(danger.find('.stat-card__value').classes()).toContain('stat-card__value--danger')

    const accent = mount(StatCard, {
      ...mountOptions,
      props: { label: '任务', value: 5, valueTone: 'accent' },
    })
    expect(accent.find('.stat-card__value').classes()).toContain('stat-card__value--accent')

    // 默认不添加强调 class
    const normal = mount(StatCard, {
      ...mountOptions,
      props: { label: '任务', value: 5 },
    })
    expect(normal.find('.stat-card__value').classes()).not.toContain('stat-card__value--danger')
  })
})
