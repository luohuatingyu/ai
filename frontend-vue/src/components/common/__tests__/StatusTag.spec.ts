import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import StatusTag from '../StatusTag.vue'
import { JOB_STATUS } from '@/utils/status'

describe('StatusTag', () => {
  const mountOptions = { global: { plugins: [ElementPlus] } }

  it('已登记状态显示中文标签', () => {
    const wrapper = mount(StatusTag, {
      ...mountOptions,
      props: { status: 'running', map: JOB_STATUS },
    })
    expect(wrapper.text()).toBe('运行中')
  })

  it('未知状态原样展示不崩溃', () => {
    const wrapper = mount(StatusTag, {
      ...mountOptions,
      props: { status: 'new_backend_state', map: JOB_STATUS },
    })
    expect(wrapper.text()).toBe('new_backend_state')
  })

  it('空状态显示占位符', () => {
    const wrapper = mount(StatusTag, {
      ...mountOptions,
      props: { status: '', map: JOB_STATUS },
    })
    expect(wrapper.text()).toBe('-')
  })

  it('所有状态渲染语义圆点', () => {
    const wrapper = mount(StatusTag, {
      ...mountOptions,
      props: { status: 'completed', map: JOB_STATUS },
    })
    expect(wrapper.find('.status-tag__dot').exists()).toBe(true)
    // 静态状态无呼吸动画 class
    expect(wrapper.find('.status-tag').classes()).not.toContain('is-active')
  })

  it('进行中状态圆点带呼吸动画 class', () => {
    const running = mount(StatusTag, {
      ...mountOptions,
      props: { status: 'running', map: JOB_STATUS },
    })
    expect(running.find('.status-tag').classes()).toContain('is-active')

    const starting = mount(StatusTag, {
      ...mountOptions,
      props: { status: 'starting', map: JOB_STATUS },
    })
    expect(starting.find('.status-tag').classes()).toContain('is-active')
  })
})
