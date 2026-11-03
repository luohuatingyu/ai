<template>
  <div class="workbench-session">
    <!-- 顶部工具栏：会话信息 + 内核与单元格操作 -->
    <div class="session-toolbar">
      <div class="toolbar-left">
        <el-button @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回列表
        </el-button>
        <h2 class="session-title">{{ session?.name || '加载中...' }}</h2>
        <StatusTag :status="session?.status" :map="SESSION_STATUS" />
      </div>

      <div class="toolbar-right">
        <el-button-group>
          <el-button
            :type="kernelStatus === 'running' ? 'warning' : 'primary'"
            @click="toggleKernel"
            :loading="kernelLoading"
          >
            <el-icon v-if="kernelStatus === 'running'"><VideoPause /></el-icon>
            <el-icon v-else><VideoPlay /></el-icon>
            {{ kernelStatus === 'running' ? '停止内核' : '启动内核' }}
          </el-button>
          <el-button @click="runAllCells" :loading="runningAll" :disabled="kernelStatus !== 'running'">
            <el-icon><CaretRight /></el-icon>
            全部运行
          </el-button>
        </el-button-group>
        <el-button @click="addCell('code')" :disabled="kernelStatus !== 'running'">
          <el-icon><Plus /></el-icon>
          代码
        </el-button>
        <el-button @click="addCell('markdown')" :disabled="kernelStatus !== 'running'">
          <el-icon><Document /></el-icon>
          Markdown
        </el-button>
        <el-button @click="showVariables = true">
          <el-icon><DataAnalysis /></el-icon>
          变量
        </el-button>
        <el-dropdown trigger="click">
          <el-button>
            <el-icon><More /></el-icon>
            更多
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="saveNotebook">
                <el-icon><DocumentChecked /></el-icon>保存 Notebook
              </el-dropdown-item>
              <el-dropdown-item @click="exportNotebook">
                <el-icon><Download /></el-icon>导出 .ipynb
              </el-dropdown-item>
              <el-dropdown-item divided @click="clearAllOutputs">
                <el-icon><Delete /></el-icon>清空所有输出
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <!-- 运行信息条 -->
    <div class="session-info-bar">
      <span class="info-item"><span class="info-label">内核</span>{{ kernelStatus === 'running' ? '已连接' : '未连接' }}</span>
      <span class="info-item"><span class="info-label">运行环境</span>{{ session?.kernel_type || '-' }}</span>
      <span class="info-item"><span class="info-label">单元格</span>{{ cells.length }} 个（已执行 {{ executedCount }}）</span>
      <span class="info-item"><span class="info-label">绑定资源</span>{{ session?.bound_workers?.length || 0 }} 个 Worker</span>
    </div>

    <!-- 变量查看器 -->
    <el-drawer v-model="showVariables" direction="rtl" title="变量查看器" size="360px">
      <div class="variables-panel">
        <el-input v-model="variableFilter" placeholder="搜索变量" size="small" class="mb-12" clearable :prefix-icon="Search" />
        <div v-if="Object.keys(variables).length === 0" class="empty-variables">
          暂无变量，执行代码后将显示在这里
        </div>
        <div v-else class="variable-list">
          <div class="variable-item" v-for="(value, name) in filteredVariables" :key="name">
            <span class="variable-name">{{ name }}</span>
            <span class="variable-type">{{ getTypeLabel(value) }}</span>
            <span class="variable-value">{{ formatValue(value) }}</span>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- 单元格列表 -->
    <div class="cells-container" v-loading="loadingSession">
      <EmptyState
        v-if="!loadingSession && cells.length === 0"
        title="暂无单元格"
        description="添加代码或 Markdown 单元格开始工作"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="addCell('code')">添加代码单元格</el-button>
        </template>
      </EmptyState>

      <div v-for="(cell, index) in cells" :key="cell.id" class="code-cell" :class="{ 'is-executing': executingCells.includes(index) }">
        <!-- 单元格工具栏 -->
        <div class="cell-toolbar">
          <div class="cell-type-badge" :class="cell.cell_type">
            {{ cell.cell_type === 'code' ? 'Code' : 'MD' }}
          </div>
          <div class="cell-execution" v-if="cell.cell_type === 'code'">
            <el-icon v-if="executingCells.includes(index)" class="is-loading executing-icon"><Loading /></el-icon>
            <span v-else-if="cell.execution_count !== null" class="execution-count">[{{ cell.execution_count }}]</span>
            <el-tag v-else size="small" effect="light" disable-transitions>未执行</el-tag>
            <el-tag v-if="cell.status === 'error'" size="small" type="danger" effect="light" disable-transitions>出错</el-tag>
          </div>
          <div class="cell-actions">
            <el-tooltip content="上移" placement="top">
              <el-button size="small" text :disabled="index === 0" @click="moveCell(index, -1)">
                <el-icon><ArrowUp /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="下移" placement="top">
              <el-button size="small" text :disabled="index === cells.length - 1" @click="moveCell(index, 1)">
                <el-icon><ArrowDown /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="复制单元格" placement="top">
              <el-button size="small" text @click="duplicateCell(index)">
                <el-icon><DocumentCopy /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="删除单元格" placement="top">
              <el-button size="small" text type="danger" @click="deleteCell(index)">
                <el-icon><Delete /></el-icon>
              </el-button>
            </el-tooltip>
          </div>
        </div>

        <!-- 代码单元格：Monaco 编辑器 + 执行 + 输出 -->
        <div v-if="cell.cell_type === 'code'" class="cell-editor">
          <div :ref="(el) => setEditorRef(cell.id, el)" class="monaco-editor"></div>

          <div class="cell-execute-bar">
            <el-button
              type="primary"
              @click="executeCell(index)"
              :disabled="kernelStatus !== 'running' || executingCells.includes(index)"
              :loading="executingCells.includes(index)"
            >
              <el-icon><VideoPlay /></el-icon>
              运行
            </el-button>
            <el-button @click="interruptCell(index)" :disabled="!executingCells.includes(index)">
              <el-icon><VideoPause /></el-icon>
              中断
            </el-button>
          </div>

          <!-- 输出区域 -->
          <div v-if="cell.outputs && cell.outputs.length > 0" class="cell-output">
            <div class="output-header">
              <span>输出</span>
              <el-button size="small" text @click="clearCellOutput(index)">
                <el-icon><Delete /></el-icon>
                清空
              </el-button>
            </div>
            <div class="output-content">
              <div v-for="(output, oi) in cell.outputs" :key="oi" class="output-item">
                <div v-if="output.output_type === 'stream'" class="stream-output">
                  <span class="stream-name">{{ output.name }}: </span>
                  <span class="stream-text">{{ output.text }}</span>
                </div>
                <div v-else-if="output.output_type === 'execute_result'" class="execute-result">
                  <pre v-if="output.data['text/plain']">{{ output.data['text/plain'] }}</pre>
                  <div v-else-if="output.data['text/html']" v-html="output.data['text/html']" />
                  <img v-else-if="output.data['image/png']" :src="'data:image/png;base64,' + output.data['image/png']" alt="执行结果图片" />
                </div>
                <div v-else-if="output.output_type === 'display_data'" class="display-data">
                  <div v-if="output.data['text/html']" v-html="output.data['text/html']" />
                  <img v-else-if="output.data['image/png']" :src="'data:image/png;base64,' + output.data['image/png']" alt="展示图片" />
                  <pre v-else-if="output.data['text/plain']">{{ output.data['text/plain'] }}</pre>
                </div>
                <div v-else-if="output.output_type === 'error'" class="error-output">
                  <div class="error-name">{{ output.ename }}: {{ output.evalue }}</div>
                  <pre class="error-traceback">{{ output.traceback?.join('\n') }}</pre>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Markdown 单元格：预览 / 编辑 -->
        <div v-else class="markdown-cell">
          <div v-if="editingCells.includes(index)" class="markdown-editor">
            <textarea v-model="cell.source" @blur="saveCell(index)" @keydown.esc="cancelEdit(index)" rows="10" spellcheck="false" />
            <div class="editor-actions">
              <el-button size="small" @click="saveCell(index)">保存</el-button>
              <el-button size="small" type="info" @click="cancelEdit(index)">取消</el-button>
            </div>
          </div>
          <div v-else class="markdown-preview" v-html="renderedMarkdown(cell.source)" @dblclick="startEdit(index)" />
          <div v-if="!editingCells.includes(index)" class="markdown-hint">
            <el-button size="small" text @click="startEdit(index)">
              <el-icon><EditPen /></el-icon>
              编辑
            </el-button>
            <span class="text-xs text-ink-400">双击也可进入编辑</span>
          </div>
        </div>
      </div>

      <!-- 底部添加 -->
      <div class="add-cell-bottom" @click="addCell('code')">
        <el-icon><Plus /></el-icon>
        <span>添加代码单元格</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Notebook 会话详情：Monaco 编辑区、执行工具栏、输出区与运行信息。
 * - 编辑器容器通过 ref 映射（cellId → DOM）定位，修复原先依赖
 *   :nth-of-type 选择器在重排/新增后命中错误容器的问题；
 * - 顶部"全部运行"按顺序执行所有代码单元格（复用单格执行接口）；
 * - Markdown 单元格提供显式编辑按钮（原仅双击触发，不易发现）；
 * - 变量查看器每 5 秒刷新，页面卸载时清理定时器与编辑器。
 */
import { ref, onMounted, onUnmounted, computed, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { marked } from 'marked'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { SESSION_STATUS } from '@/utils/status'
import { Search, Loading, EditPen, CaretRight, DataAnalysis } from '@element-plus/icons-vue'
import {
  ArrowLeft, VideoPlay, VideoPause, Plus, Document, More, DocumentChecked, Download, Delete,
  ArrowUp, ArrowDown, DocumentCopy,
} from '@element-plus/icons-vue'

// Monaco Editor 动态导入（测试环境通过 vi.mock 打桩）
let monaco: any = null
const loadMonaco = async () => {
  if (!monaco) {
    const mod = await import('monaco-editor')
    monaco = mod.default
    monaco.editor.defineTheme('mtp-dark', {
      base: 'vs-dark',
      inherit: true,
      rules: [],
      colors: {
        'editor.background': '#1e1e1e',
        'editor.foreground': '#d4d4d4',
      },
    })
  }
  return monaco
}

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const sessionId = route.params.id as string
const session = ref<any>(null)
const cells = ref<any[]>([])
const loadingSession = ref(false)
const kernelStatus = ref('stopped')
const kernelLoading = ref(false)
const executingCells = ref<number[]>([])
const runningAll = ref(false)
const editingCells = ref<number[]>([])
const showVariables = ref(false)
const variables = ref<Record<string, any>>({})
const variableFilter = ref('')

/** 编辑器实例映射（cellId → monaco editor） */
const editors = ref<Map<string, any>>(new Map())
/** 编辑器容器 DOM 映射（cellId → HTMLElement） */
const editorContainers = ref<Map<string, HTMLElement>>(new Map())

/** 已执行单元格数量（运行信息条） */
const executedCount = computed(() =>
  cells.value.filter((c) => c.cell_type === 'code' && c.execution_count !== null).length
)

/** 按变量名过滤后的变量列表 */
const filteredVariables = computed(() => {
  if (!variableFilter.value) return variables.value
  const filter = variableFilter.value.toLowerCase()
  const result: Record<string, any> = {}
  for (const [k, v] of Object.entries(variables.value)) {
    if (k.toLowerCase().includes(filter)) result[k] = v
  }
  return result
})

/** 编辑器容器 ref 收集器（模板中使用） */
const setEditorRef = (cellId: string, el: any) => {
  if (el) editorContainers.value.set(cellId, el as HTMLElement)
  else editorContainers.value.delete(cellId)
}

const fetchSession = async () => {
  loadingSession.value = true
  try {
    const res: any = await api.get(`/workbench/sessions/${sessionId}`)
    session.value = res
    kernelStatus.value = res.status
    cells.value = res.cells || []
    await nextTick()
    initializeEditors()
  } catch (err: any) {
    appStore.error('加载失败', err?.message)
    router.push('/workbench/sessions')
  } finally {
    loadingSession.value = false
  }
}

const fetchVariables = async () => {
  try {
    const res: any = await api.get(`/workbench/sessions/${sessionId}/variables`)
    variables.value = res.variables || {}
  } catch (err) {
    console.error('获取变量失败', err)
  }
}

/** 为所有代码单元格创建编辑器 */
const initializeEditors = async () => {
  await loadMonaco()
  for (const cell of cells.value) {
    if (cell.cell_type === 'code') {
      createEditor(cell.id)
    }
  }
}

/** 在指定单元格容器上创建 Monaco 编辑器 */
const createEditor = (cellId: string) => {
  const container = editorContainers.value.get(cellId)
  if (!container || editors.value.has(cellId)) return

  const cell = cells.value.find((c) => c.id === cellId)
  const editor = monaco.editor.create(container, {
    value: cell?.source || '',
    language: 'python',
    theme: 'mtp-dark',
    automaticLayout: true,
    minimap: { enabled: false },
    lineNumbers: 'on',
    fontSize: 13,
    tabSize: 4,
    insertSpaces: true,
    wordWrap: 'on',
  })

  editor.onDidChangeModelContent(() => {
    const target = cells.value.find((c) => c.id === cellId)
    if (target) target.source = editor.getValue()
  })

  editors.value.set(cellId, editor)
}

/** 销毁指定单元格的编辑器 */
const destroyEditor = (cellId: string) => {
  const editor = editors.value.get(cellId)
  if (editor) {
    editor.dispose()
    editors.value.delete(cellId)
  }
}

const toggleKernel = async () => {
  kernelLoading.value = true
  try {
    if (kernelStatus.value === 'running') {
      await api.post(`/workbench/sessions/${sessionId}/kernel/stop`)
      kernelStatus.value = 'stopped'
      appStore.success('内核已停止')
    } else {
      await api.post(`/workbench/sessions/${sessionId}/kernel/start`)
      kernelStatus.value = 'running'
      appStore.success('内核已启动')
    }
  } catch (err: any) {
    appStore.error('操作失败', err?.message)
  } finally {
    kernelLoading.value = false
  }
}

/** 新增单元格（默认追加到末尾） */
const addCell = (type: 'code' | 'markdown', afterIndex?: number) => {
  const newCell = {
    id: `cell-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`,
    cell_type: type,
    source: '',
    outputs: [],
    execution_count: null,
    metadata: {},
  }

  const insertIndex = afterIndex !== undefined ? afterIndex + 1 : cells.value.length
  cells.value.splice(insertIndex, 0, newCell)

  nextTick(() => {
    if (type === 'code') {
      createEditor(newCell.id)
    }
  })
}

/** 执行单个代码单元格 */
const executeCell = async (index: number) => {
  if (kernelStatus.value !== 'running') {
    appStore.warning('请先启动内核')
    return
  }

  const cell = cells.value[index]
  if (!cell.source.trim()) return

  executingCells.value.push(index)

  try {
    const result: any = await api.post(`/workbench/sessions/${sessionId}/cells/execute`, {
      cell_id: cell.id,
      cell_index: index,
      code: cell.source,
      cell_type: 'code',
      mode: 'single',
    })

    cell.outputs = result.outputs || []
    cell.execution_count = result.execution_count
    cell.status = result.success ? 'completed' : 'error'
    if (result.error) cell.error = result.error

    if (result.variables) {
      variables.value = { ...variables.value, ...result.variables }
    }

    if (!result.success) {
      appStore.error('执行失败', result.error)
    }
  } catch (err: any) {
    appStore.error('执行失败', err?.message)
    cell.outputs = [{ output_type: 'error', ename: 'Error', evalue: err?.message, traceback: [err?.message] }]
  } finally {
    executingCells.value = executingCells.value.filter((i) => i !== index)
  }
}

/** 顺序执行全部代码单元格（任一失败继续执行后续单元格） */
const runAllCells = async () => {
  if (kernelStatus.value !== 'running') {
    appStore.warning('请先启动内核')
    return
  }
  runningAll.value = true
  try {
    for (let i = 0; i < cells.value.length; i++) {
      const cell = cells.value[i]
      if (cell.cell_type === 'code' && cell.source.trim() && !executingCells.value.includes(i)) {
        await executeCell(i)
      }
    }
  } finally {
    runningAll.value = false
  }
}

const interruptCell = async (index: number) => {
  try {
    await api.post(`/workbench/sessions/${sessionId}/cells/interrupt`)
    executingCells.value = executingCells.value.filter((i) => i !== index)
    appStore.success('已中断')
  } catch (err: any) {
    appStore.error('中断失败', err?.message)
  }
}

/** 保存单元格内容（Markdown 编辑退出时调用） */
const saveCell = async (index: number) => {
  const cell = cells.value[index]
  try {
    await api.put(`/workbench/sessions/${sessionId}/cells`, {
      cell_id: cell.id,
      source: cell.source,
      cell_type: cell.cell_type,
      cell_index: index,
    })
    editingCells.value = editingCells.value.filter((i) => i !== index)
    appStore.success('保存成功')
  } catch (err: any) {
    appStore.error('保存失败', err?.message)
  }
}

const startEdit = (index: number) => {
  if (cells.value[index].cell_type === 'markdown') {
    if (!editingCells.value.includes(index)) editingCells.value.push(index)
  }
}

const cancelEdit = (index: number) => {
  editingCells.value = editingCells.value.filter((i) => i !== index)
}

const deleteCell = (index: number) => {
  const cell = cells.value[index]
  if (cell.cell_type === 'code') {
    destroyEditor(cell.id)
  }
  cells.value.splice(index, 1)
}

/** 复制单元格（新 id，内容一致） */
const duplicateCell = (index: number) => {
  const cell = { ...cells.value[index], id: `cell-${Date.now()}-${Math.random().toString(36).slice(2, 11)}` }
  cells.value.splice(index + 1, 0, cell)
  nextTick(() => {
    if (cell.cell_type === 'code') {
      createEditor(cell.id)
    }
  })
}

/** 上下移动单元格：按新顺序销毁并重建全部编辑器，保证内容对位 */
const moveCell = (index: number, direction: number) => {
  const newIndex = index + direction
  if (newIndex < 0 || newIndex >= cells.value.length) return
  ;[cells.value[index], cells.value[newIndex]] = [cells.value[newIndex], cells.value[index]]

  editors.value.forEach((editor) => editor.dispose())
  editors.value.clear()
  nextTick(async () => {
    await nextTick()
    initializeEditors()
  })
}

const clearCellOutput = async (index: number) => {
  cells.value[index].outputs = []
  cells.value[index].execution_count = null
  try {
    await api.put(`/workbench/sessions/${sessionId}/cells`, {
      cell_id: cells.value[index].id,
      source: cells.value[index].source,
      cell_type: 'code',
      cell_index: index,
    })
  } catch (err) {
    console.error('清空输出失败', err)
  }
}

/** 保存整个 Notebook（全部单元格） */
const saveNotebook = async () => {
  try {
    await api.put(`/workbench/sessions/${sessionId}/notebook`, {
      cells: cells.value.map((c) => ({
        cell_type: c.cell_type,
        source: c.source,
        outputs: c.outputs,
        execution_count: c.execution_count,
        metadata: c.metadata,
      })),
    })
    appStore.success('Notebook 保存成功')
  } catch (err: any) {
    appStore.error('保存失败', err?.message)
  }
}

/** 导出为标准 .ipynb 文件 */
const exportNotebook = async () => {
  try {
    const content = {
      nbformat: 4,
      nbformat_minor: 4,
      metadata: { kernelspec: { name: 'python3', display_name: 'Python 3' } },
      cells: cells.value.map((c) => ({
        cell_type: c.cell_type,
        source: c.source.split('\n'),
        outputs: c.outputs || [],
        execution_count: c.execution_count,
        metadata: {},
      })),
    }
    const blob = new Blob([JSON.stringify(content, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${session.value?.name || 'notebook'}.ipynb`
    a.click()
    URL.revokeObjectURL(url)
    appStore.success('导出成功')
  } catch (err: any) {
    appStore.error('导出失败', err?.message)
  }
}

const clearAllOutputs = async () => {
  for (const cell of cells.value) {
    if (cell.cell_type === 'code') {
      cell.outputs = []
      cell.execution_count = null
    }
  }
  await saveNotebook()
  appStore.success('已清空所有输出')
}

/** 变量类型标签 */
const getTypeLabel = (value: any) => {
  if (value === null) return 'null'
  if (Array.isArray(value)) return `Array[${value.length}]`
  if (typeof value === 'object') return `Object{${Object.keys(value).length}}`
  return typeof value
}

/** 变量值预览（截断到 100 字符） */
const formatValue = (value: any) => {
  if (value === null) return 'null'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value).slice(0, 100) + (JSON.stringify(value).length > 100 ? '...' : '')
    } catch {
      return String(value)
    }
  }
  return String(value).slice(0, 100)
}

/** Markdown 渲染（v-html 展示） */
const renderedMarkdown = (source: string) => {
  return marked.parse(source || '')
}

const goBack = () => router.push('/workbench/sessions')

// 变量轮询与编辑器清理
let variablesTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  // 并行加载：会话数据不等待编辑器库下载（弱网下先展示数据，编辑器就绪后再挂载）
  const monacoPromise = loadMonaco()
  await fetchSession()
  await monacoPromise
  await nextTick()
  initializeEditors()

  await fetchVariables()
  // 定期刷新变量
  variablesTimer = setInterval(fetchVariables, 5000)
})

onUnmounted(() => {
  if (variablesTimer) {
    clearInterval(variablesTimer)
    variablesTimer = null
  }
  editors.value.forEach((editor) => editor.dispose())
  editors.value.clear()
})
</script>

<style scoped>
.workbench-session {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--mtp-header-h) - 52px);
  min-height: 480px;
}

/* 工具栏 */
.session-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: var(--mtp-bg-card);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-lg);
  box-shadow: var(--mtp-shadow-card);
  flex-wrap: wrap;
  gap: 12px;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.session-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.toolbar-right .el-button + .el-button {
  margin-left: 0;
}

/* 运行信息条 */
.session-info-bar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 8px 16px;
  margin-top: 10px;
  background: var(--mtp-bg-subtle);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-md);
  font-size: 12.5px;
  color: var(--mtp-ink-700);
  flex-wrap: wrap;
}

.info-label {
  color: var(--mtp-ink-400);
  margin-right: 6px;
}

.info-item {
  display: inline-flex;
  align-items: center;
}

/* 单元格区域 */
.cells-container {
  flex: 1;
  overflow: auto;
  padding: 14px 2px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-top: 10px;
}

.code-cell {
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-lg);
  background: var(--mtp-bg-card);
  overflow: hidden;
  box-shadow: var(--mtp-shadow-card);
  transition: border-color 0.2s;
}

.code-cell.is-executing {
  border-color: var(--mtp-brand-500);
}

.cell-toolbar {
  display: flex;
  align-items: center;
  padding: 6px 12px;
  background: var(--mtp-bg-subtle);
  border-bottom: 1px solid var(--mtp-ink-100);
  gap: 12px;
}

.cell-type-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  min-width: 44px;
  text-align: center;
}

.cell-type-badge.code {
  background: var(--mtp-brand-100);
  color: var(--mtp-brand-600);
}

.cell-type-badge.markdown {
  background: #e7f6ef;
  color: var(--mtp-success);
}

.cell-execution {
  display: flex;
  align-items: center;
  gap: 8px;
}

.executing-icon {
  color: var(--mtp-brand-600);
}

.execution-count {
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  color: var(--mtp-ink-500);
}

.cell-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}

.cell-editor {
  position: relative;
  min-height: 120px;
}

.cell-execute-bar {
  display: flex;
  gap: 8px;
  padding: 8px 12px;
  background: var(--mtp-bg-subtle);
  border-top: 1px solid var(--mtp-ink-100);
}

.cell-output {
  border-top: 1px solid var(--mtp-ink-100);
  background: #1e1e2a;
}

.output-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: #2a2a3a;
  color: #ccc;
  font-size: 12px;
}

.output-content {
  padding: 12px;
  max-height: 400px;
  overflow: auto;
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  line-height: 1.6;
}

.output-item {
  margin-bottom: 8px;
}

.output-item:last-child {
  margin-bottom: 0;
}

.stream-output {
  color: #85e89d;
  white-space: pre-wrap;
  word-break: break-word;
}

.stream-name {
  color: #9cdcfe;
}

.execute-result {
  color: #d4d4d4;
}

.error-output {
  color: #f14c4c;
}

.error-name {
  font-weight: bold;
  margin-bottom: 4px;
}

.error-traceback {
  font-size: 11px;
  white-space: pre-wrap;
}

/* Markdown 单元格 */
.markdown-cell {
  position: relative;
}

.markdown-editor {
  padding: 12px;
  border-bottom: 1px solid var(--mtp-ink-100);
}

.markdown-editor textarea {
  width: 100%;
  border: 1px solid var(--mtp-ink-200);
  border-radius: var(--mtp-radius-sm);
  padding: 10px;
  font-family: var(--mtp-font-mono);
  font-size: 13px;
  resize: vertical;
  outline: none;
}

.markdown-editor textarea:focus {
  border-color: var(--mtp-brand-500);
}

.editor-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 8px;
}

.markdown-preview {
  padding: 16px 16px 6px;
  min-height: 60px;
  cursor: pointer;
}

.markdown-preview:hover {
  background: var(--mtp-bg-subtle);
}

.markdown-preview :deep(h1),
.markdown-preview :deep(h2),
.markdown-preview :deep(h3) {
  margin-top: 16px;
  margin-bottom: 8px;
}

.markdown-preview :deep(code) {
  background: var(--mtp-bg-subtle);
  padding: 2px 4px;
  border-radius: 3px;
  font-family: var(--mtp-font-mono);
}

.markdown-preview :deep(pre) {
  background: #1e1e2a;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 4px;
  overflow: auto;
}

.markdown-preview :deep(pre code) {
  background: transparent;
  padding: 0;
}

.markdown-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px 8px;
}

/* 底部添加按钮 */
.add-cell-bottom {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 18px;
  color: var(--mtp-ink-400);
  border: 2px dashed var(--mtp-ink-200);
  border-radius: var(--mtp-radius-lg);
  cursor: pointer;
  transition: all 0.2s;
  background: transparent;
}

.add-cell-bottom:hover {
  border-color: var(--mtp-brand-500);
  color: var(--mtp-brand-600);
  background: var(--mtp-brand-50);
}

/* 变量面板 */
.variable-list {
  max-height: 60vh;
  overflow: auto;
}

.variable-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--mtp-ink-100);
  font-size: 13px;
  gap: 12px;
}

.variable-name {
  font-weight: 500;
  min-width: 120px;
}

.variable-type {
  color: var(--mtp-ink-400);
  font-size: 11px;
  background: var(--mtp-bg-subtle);
  padding: 1px 6px;
  border-radius: 3px;
}

.variable-value {
  margin-left: auto;
  font-family: var(--mtp-font-mono);
  font-size: 11px;
  color: var(--mtp-ink-500);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-variables {
  text-align: center;
  padding: 40px;
  color: var(--mtp-ink-400);
}
</style>
