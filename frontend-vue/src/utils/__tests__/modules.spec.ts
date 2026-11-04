import { describe, it, expect } from 'vitest'
import { MODULES, moduleKeyFromPath, getModule } from '../modules'

describe('模块视觉标识注册表', () => {
  it('覆盖 7 个业务域 + 总览，且关键字段完整', () => {
    const keys = Object.keys(MODULES)
    expect(keys).toEqual(expect.arrayContaining([
      'dashboard', 'datasets', 'workbench', 'templates',
      'training', 'models', 'pipelines', 'clusters',
    ]))
    for (const meta of Object.values(MODULES)) {
      // 强调色、浅底色、暗背景变体、图标、路由前缀缺一不可
      expect(meta.accent).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(meta.accentSoft).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(meta.accentLight).toMatch(/^#[0-9a-fA-F]{6}$/)
      expect(meta.icon).toBeTruthy()
      expect(meta.prefix).toMatch(/^\//)
    }
  })

  it('moduleKeyFromPath：按路由一级前缀推导模块', () => {
    expect(moduleKeyFromPath('/training/detail/job-1')).toBe('training')
    expect(moduleKeyFromPath('/datasets/list')).toBe('datasets')
    expect(moduleKeyFromPath('/pipelines/run/p1/r1')).toBe('pipelines')
    expect(moduleKeyFromPath('/dashboard')).toBe('dashboard')
  })

  it('moduleKeyFromPath：未登记前缀返回 undefined', () => {
    expect(moduleKeyFromPath('/unknown/page')).toBeUndefined()
    expect(moduleKeyFromPath('/')).toBeUndefined()
  })

  it('getModule：按 key 取模块，空值 / 未登记返回 undefined', () => {
    expect(getModule('training')?.label).toBe('训练任务')
    expect(getModule('nope')).toBeUndefined()
    expect(getModule('')).toBeUndefined()
    expect(getModule(undefined)).toBeUndefined()
  })
})
