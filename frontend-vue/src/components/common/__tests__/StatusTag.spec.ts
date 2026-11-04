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
})
