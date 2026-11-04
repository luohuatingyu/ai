import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import PageShell from '../PageShell.vue'

describe('PageShell', () => {
  const mountOptions = { global: { plugins: [ElementPlus] } }

  it('渲染面包屑（首页 / 模块 / 标题）、标题与描述', () => {
    const wrapper = mount(PageShell, {
      ...mountOptions,
      props: { title: '数据集列表', module: '数据集管理', description: '统一管理平台数据集' },
    })
    const text = wrapper.text()
    expect(text).toContain('首页')
    expect(text).toContain('数据集管理')
    expect(text).toContain('数据集列表')
    expect(text).toContain('统一管理平台数据集')
  })

  it('actions 插槽渲染在页面头部', () => {
    const wrapper = mount(PageShell, {
      ...mountOptions,
      props: { title: 'T' },
      slots: { actions: '<button class="page-action-btn">导入数据集</button>' },
    })
    expect(wrapper.find('.page-action-btn').exists()).toBe(true)
  })

  it('loading 时展示遮罩', () => {
    const wrapper = mount(PageShell, {
      ...mountOptions,
      props: { title: 'T', loading: true },
    })
    expect(wrapper.find('.el-loading-mask').exists()).toBe(true)
  })

  it('传入 moduleKey 时渲染模块章并注入模块强调色 CSS 变量', () => {
    const wrapper = mount(PageShell, {
      ...mountOptions,
      props: { title: '任务列表', moduleKey: 'training' },
    })
    expect(wrapper.find('.page-title__chip').exists()).toBe(true)
    const style = (wrapper.find('.page-view').element as HTMLElement).style
    expect(style.getPropertyValue('--mtp-module-accent')).toBe('#3056d3')
    expect(style.getPropertyValue('--mtp-module-accent-soft')).toBe('#e8eefc')
  })

  it('未传 moduleKey（无路由上下文）时不渲染模块章，样式变量缺省', () => {
    const wrapper = mount(PageShell, {
      ...mountOptions,
      props: { title: 'T' },
    })
    expect(wrapper.find('.page-title__chip').exists()).toBe(false)
    const style = (wrapper.find('.page-view').element as HTMLElement).style
    expect(style.getPropertyValue('--mtp-module-accent')).toBe('')
  })
})
