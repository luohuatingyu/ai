import { mount, flushPromises } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import type { RouteRecordRaw, Router } from 'vue-router'
import { createPinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'

/**
 * 页面测试挂载助手：
 * - 提供内存路由 / 独立 Pinia / Element Plus 全量组件；
 * - happy-dom 缺少 ResizeObserver 时补充最小桩（el-table 布局依赖）。
 */
if (typeof globalThis.ResizeObserver === 'undefined') {
  Object.assign(globalThis, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  })
}

export interface MountPageResult {
  wrapper: VueWrapper<any>
  pinia: Pinia
  router: Router
}

export async function mountPage(
  component: any,
  options: { routes?: RouteRecordRaw[]; path?: string; pinia?: Pinia } = {}
): Promise<MountPageResult> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: options.routes ?? [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  // 必须触发一次导航：未发起任何导航时 isReady() 会一直等待，导致测试挂起
  router.push(options.path ?? '/')
  await router.isReady()

  const pinia = options.pinia ?? createPinia()
  const wrapper = mount(component, {
    global: { plugins: [ElementPlus, router, pinia] },
  })
  // 多轮 flush：覆盖动态 import（如 monaco 桩）与接口响应的异步链路
  await flushPromises()
  await flushPromises()
  await flushPromises()
  return { wrapper, pinia, router }
}
