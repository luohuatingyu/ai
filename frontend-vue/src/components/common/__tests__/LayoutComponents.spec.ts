import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import FilterBar from '../FilterBar.vue'
import SectionCard from '../SectionCard.vue'

describe('FilterBar', () => {
  it('渲染主筛选区与右侧操作区', () => {
    const wrapper = mount(FilterBar, {
      global: { plugins: [ElementPlus] },
      slots: {
        default: '<input class="filter-input" />',
        actions: '<button class="filter-export">导出</button>',
      },
    })
    expect(wrapper.find('.filter-input').exists()).toBe(true)
    expect(wrapper.find('.filter-export').exists()).toBe(true)
  })
})

describe('SectionCard', () => {
  const mountOptions = { global: { plugins: [ElementPlus] } }

  it('渲染标题、描述与 extra 插槽', () => {
    const wrapper = mount(SectionCard, {
      ...mountOptions,
      props: { title: '最近任务', description: '最新 5 条' },
      slots: { extra: '<button class="extra-btn">查看全部</button>', default: '<div>body</div>' },
    })
    expect(wrapper.text()).toContain('最近任务')
    expect(wrapper.text()).toContain('最新 5 条')
    expect(wrapper.find('.extra-btn').exists()).toBe(true)
    expect(wrapper.text()).toContain('body')
  })

  it('flush 模式内容区无内边距（用于表格通栏）', () => {
    const wrapper = mount(SectionCard, {
      ...mountOptions,
      props: { title: 'T', flush: true },
    })
    expect(wrapper.find('.section-card__body').classes()).toContain('is-flush')
  })

  it('tip 属性渲染提示图标', () => {
    const wrapper = mount(SectionCard, {
      ...mountOptions,
      props: { title: 'T', tip: '说明文字' },
    })
    expect(wrapper.find('.section-card__tip').exists()).toBe(true)
  })
})
