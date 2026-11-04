/**
 * Pipeline DAG 工具：依赖校验、分层布局与画布图构建。
 * 纯函数实现，独立于 UI，便于完整单元测试（重复 ID / 自依赖 /
 * 缺失依赖 / 循环依赖 / 分支图）。
 */

/** 最小步骤形状（PipelineCreate 的 StepDraft 与后端 step 均兼容） */
export interface DagStep {
  id: string
  name?: string
  depends_on?: string[]
}

/** 画布节点位置 */
export interface DagPosition {
  x: number
  y: number
}

/** 画布节点（Vue Flow 兼容形状） */
export interface DagNode {
  id: string
  label: string
  type?: string
  position: DagPosition
  /** 附加样式类（如执行状态着色） */
  class?: string
}

/** 画布边（依赖 → 步骤） */
export interface DagEdge {
  id: string
  source: string
  target: string
}

/** 依赖问题类型 */
export type DagIssueType = 'duplicate_id' | 'empty_id' | 'self_dep' | 'missing_dep' | 'cycle'

/** 一条依赖问题 */
export interface DagIssue {
  type: DagIssueType
  message: string
}

/**
 * 校验步骤依赖关系，返回全部问题（空数组表示合法 DAG）。
 * 检查项：重复 ID、空 ID、自依赖、依赖不存在的步骤、循环依赖。
 */
export function detectIssues(steps: DagStep[]): DagIssue[] {
  const issues: DagIssue[] = []
  const idCounts = new Map<string, number>()
  for (const step of steps) {
    idCounts.set(step.id, (idCounts.get(step.id) || 0) + 1)
    if (!step.id) issues.push({ type: 'empty_id', message: '存在缺少 ID 的步骤' })
  }
  for (const [id, count] of idCounts) {
    if (id && count > 1) issues.push({ type: 'duplicate_id', message: `步骤 ID 重复：${id}（出现 ${count} 次）` })
  }

  const idSet = new Set(steps.map((s) => s.id))
  for (const step of steps) {
    const deps = step.depends_on || []
    for (const dep of deps) {
      if (dep === step.id) {
        issues.push({ type: 'self_dep', message: `步骤「${stepLabel(step)}」依赖自身` })
      } else if (!idSet.has(dep)) {
        issues.push({ type: 'missing_dep', message: `步骤「${stepLabel(step)}」依赖不存在的步骤：${dep}` })
      }
    }
  }

  // 循环依赖：对每个节点做 DFS 染色（白/灰/黑），灰节点回边即环
  const WHITE = 0
  const GRAY = 1
  const BLACK = 2
  const color = new Map<string, number>()
  const cycleNodes = new Set<string>()

  const visit = (id: string, path: string[]) => {
    color.set(id, GRAY)
    const step = steps.find((s) => s.id === id)
    for (const dep of step?.depends_on || []) {
      if (!idSet.has(dep) || dep === id) continue // 其他类型的问题已单独上报
      const c = color.get(dep) ?? WHITE
      if (c === GRAY) {
        // 找到环：从 path 中 dep 之后的部分 + dep 即环节点
        const idx = path.indexOf(dep)
        const cyclePath = idx >= 0 ? path.slice(idx) : path
        for (const n of cyclePath) cycleNodes.add(n)
        cycleNodes.add(dep)
        cycleNodes.add(id)
      } else if (c === WHITE) {
        visit(dep, [...path, id])
      }
    }
    color.set(id, BLACK)
  }

  for (const step of steps) {
    if ((color.get(step.id) ?? WHITE) === WHITE) visit(step.id, [])
  }
  if (cycleNodes.size > 0) {
    issues.push({
      type: 'cycle',
      message: `存在循环依赖，涉及步骤：${Array.from(cycleNodes).join(' → ')}`,
    })
  }

  return issues
}

/** 步骤展示名（未命名时回退到 ID） */
export function stepLabel(step: DagStep): string {
  return step.name || step.id
}

/**
 * 分层布局：按依赖深度分层（最长路径），同层按出现顺序纵向排列。
 * 前置条件：steps 已通过 detectIssues（无环）。存在环时按已算深度兜底。
 */
export function computeLayout(steps: DagStep[]): Record<string, DagPosition> {
  const positions: Record<string, DagPosition> = {}
  const depth = new Map<string, number>()

  const depthOf = (id: string, visiting: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!
    if (visiting.has(id)) return 0 // 环兜底，避免死循环
    visiting.add(id)
    const step = steps.find((s) => s.id === id)
    const deps = (step?.depends_on || []).filter((d) => steps.some((s) => s.id === d) && d !== id)
    const level = deps.length === 0 ? 0 : Math.max(...deps.map((d) => depthOf(d, visiting))) + 1
    depth.set(id, level)
    return level
  }

  for (const step of steps) depthOf(step.id, new Set())

  // 同层内按步骤声明顺序分配行号
  const columnCount = new Map<number, number>()
  for (const step of steps) {
    const level = depth.get(step.id) || 0
    const row = columnCount.get(level) || 0
    columnCount.set(level, row + 1)
    positions[step.id] = { x: level * 280, y: row * 120 }
  }
  return positions
}

/**
 * 构建画布图：节点（含布局位置）与依赖边。
 */
export function buildGraph(
  steps: DagStep[],
  options: {
    positions?: Record<string, DagPosition>
  } = {}
): { nodes: DagNode[]; edges: DagEdge[] } {
  const positions = options.positions || computeLayout(steps)
  const nodes: DagNode[] = steps.map((step) => ({
    id: step.id,
    label: stepLabel(step),
    type: (step as any).type,
    position: positions[step.id] || { x: 0, y: 0 },
  }))
  const edges: DagEdge[] = []
  for (const step of steps) {
    for (const dep of step.depends_on || []) {
      if (steps.some((s) => s.id === dep) && dep !== step.id) {
        edges.push({ id: `e-${dep}-${step.id}`, source: dep, target: step.id })
      }
    }
  }
  return { nodes, edges }
}

/**
 * 画布图 ↔ 步骤草稿的往返转换保护：
 * 校验画布节点/边集合与步骤依赖集合一致（保存前防呆）。
 */
export function graphMatchesSteps(steps: DagStep[], nodes: DagNode[], edges: DagEdge[]): boolean {
  const stepIds = new Set(steps.map((s) => s.id))
  const nodeIds = new Set(nodes.map((n) => n.id))
  if (stepIds.size !== nodeIds.size || [...stepIds].some((id) => !nodeIds.has(id))) return false
  const expectedEdges = new Set(steps.flatMap((s) => (s.depends_on || []).filter((d) => stepIds.has(d) && d !== s.id).map((d) => `${d}->${s.id}`)))
  const actualEdges = new Set(edges.map((e) => `${e.source}->${e.target}`))
  if (expectedEdges.size !== actualEdges.size) return false
  for (const e of expectedEdges) if (!actualEdges.has(e)) return false
  return true
}
