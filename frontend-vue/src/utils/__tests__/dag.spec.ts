import { describe, it, expect } from 'vitest'
import {
  detectIssues, computeLayout, buildGraph, graphMatchesSteps, stepLabel,
} from '../dag'

describe('detectIssues（依赖校验）', () => {
  it('合法 DAG（多根 + 分支 + 汇聚）无问题', () => {
    const steps = [
      { id: 'a', name: 'A', depends_on: [] },
      { id: 'b', name: 'B', depends_on: [] },
      { id: 'c', name: 'C', depends_on: ['a', 'b'] },
      { id: 'd', name: 'D', depends_on: ['a'] },
      { id: 'e', name: 'E', depends_on: ['c', 'd'] },
    ]
    expect(detectIssues(steps)).toEqual([])
  })

  it('重复 ID 被检出', () => {
    const issues = detectIssues([
      { id: 'a', depends_on: [] },
      { id: 'a', depends_on: [] },
    ])
    expect(issues.some((i) => i.type === 'duplicate_id')).toBe(true)
  })

  it('空 ID 被检出', () => {
    const issues = detectIssues([{ id: '', depends_on: [] }])
    expect(issues.some((i) => i.type === 'empty_id')).toBe(true)
  })

  it('自依赖被检出', () => {
    const issues = detectIssues([{ id: 'a', depends_on: ['a'] }])
    expect(issues.some((i) => i.type === 'self_dep')).toBe(true)
  })

  it('依赖不存在的步骤被检出', () => {
    const issues = detectIssues([{ id: 'a', depends_on: ['ghost'] }])
    expect(issues.some((i) => i.type === 'missing_dep')).toBe(true)
  })

  it('两节点循环被检出', () => {
    const issues = detectIssues([
      { id: 'a', depends_on: ['b'] },
      { id: 'b', depends_on: ['a'] },
    ])
    const cycle = issues.find((i) => i.type === 'cycle')
    expect(cycle).toBeDefined()
    expect(cycle!.message).toContain('a')
    expect(cycle!.message).toContain('b')
  })

  it('三节点循环被检出', () => {
    const issues = detectIssues([
      { id: 'a', depends_on: ['c'] },
      { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['b'] },
    ])
    expect(issues.some((i) => i.type === 'cycle')).toBe(true)
  })

  it('菱形依赖（合法）不误报循环', () => {
    const steps = [
      { id: 'a', depends_on: [] },
      { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['a'] },
      { id: 'd', depends_on: ['b', 'c'] },
    ]
    expect(detectIssues(steps)).toEqual([])
  })
})

describe('computeLayout（分层布局）', () => {
  it('线性链按深度分层', () => {
    const positions = computeLayout([
      { id: 'a', depends_on: [] },
      { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['b'] },
    ])
    expect(positions.a.x).toBe(0)
    expect(positions.b.x).toBe(280)
    expect(positions.c.x).toBe(560)
  })

  it('菱形汇聚：汇聚节点深度为最长路径', () => {
    const positions = computeLayout([
      { id: 'a', depends_on: [] },
      { id: 'b', depends_on: ['a'] },
      { id: 'c', depends_on: ['a'] },
      { id: 'd', depends_on: ['b', 'c'] },
    ])
    expect(positions.d.x).toBe(560) // a→b→d 深度 2
  })

  it('多根并行步骤同层错行', () => {
    const positions = computeLayout([
      { id: 'a', depends_on: [] },
      { id: 'b', depends_on: [] },
    ])
    expect(positions.a.x).toBe(0)
    expect(positions.b.x).toBe(0)
    expect(positions.a.y).not.toBe(positions.b.y)
  })

  it('存在环时不死循环（兜底深度 0）', () => {
    const positions = computeLayout([
      { id: 'a', depends_on: ['b'] },
      { id: 'b', depends_on: ['a'] },
    ])
    expect(positions.a).toBeDefined()
    expect(positions.b).toBeDefined()
  })
})

describe('buildGraph（画布图构建）', () => {
  it('节点与边与步骤依赖一致', () => {
    const { nodes, edges } = buildGraph([
      { id: 'a', name: '拉取', depends_on: [] },
      { id: 'b', name: '训练', depends_on: ['a'] },
      { id: 'c', name: '部署', depends_on: ['b'] },
    ])
    expect(nodes.map((n) => n.id)).toEqual(['a', 'b', 'c'])
    expect(nodes[1].label).toBe('训练')
    expect(edges).toEqual([
      { id: 'e-a-b', source: 'a', target: 'b' },
      { id: 'e-b-c', source: 'b', target: 'c' },
    ])
  })

  it('缺失依赖与自依赖不生成边', () => {
    const { edges } = buildGraph([
      { id: 'a', depends_on: ['ghost', 'a'] },
    ])
    expect(edges).toEqual([])
  })
})

describe('graphMatchesSteps（画布与步骤同步防呆）', () => {
  const steps = [
    { id: 'a', depends_on: [] },
    { id: 'b', depends_on: ['a'] },
  ]

  it('节点与边完全一致时返回 true', () => {
    const { nodes, edges } = buildGraph(steps)
    expect(graphMatchesSteps(steps, nodes, edges)).toBe(true)
  })

  it('缺少节点时返回 false', () => {
    const { nodes, edges } = buildGraph(steps)
    expect(graphMatchesSteps(steps, nodes.slice(0, 1), edges)).toBe(false)
  })

  it('缺少边时返回 false', () => {
    const { nodes } = buildGraph(steps)
    expect(graphMatchesSteps(steps, nodes, [])).toBe(false)
  })
})

describe('stepLabel', () => {
  it('未命名步骤回退到 ID', () => {
    expect(stepLabel({ id: 's1', name: '训练' })).toBe('训练')
    expect(stepLabel({ id: 's1' })).toBe('s1')
    expect(stepLabel({ id: 's1', name: '' })).toBe('s1')
  })
})
