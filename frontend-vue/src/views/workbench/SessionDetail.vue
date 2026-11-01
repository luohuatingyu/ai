<template>
  <div class="workbench-session">
    <div class="session-toolbar">
      <div class="toolbar-left">
        <el-button @click="goBack" class="back-btn">
          <el-icon><ArrowLeft /></el-icon>
          返回列表
        </el-button>
        <h2 class="session-title">{{ session?.name || '加载中...' }}</h2>
        <el-tag :type="getStatusType(session?.status)" effect="dark" size="small">
          {{ getStatusText(session?.status) }}
        </el-tag>
      </div>
      
      <div class="toolbar-center">
        <el-button-group>
          <el-button :type="kernelStatus === 'running' ? 'success' : 'primary'" 
                     @click="toggleKernel" :loading="kernelLoading">
            <el-icon v-if="kernelStatus === 'running'"><VideoPause /></el-icon>
            <el-icon v-else><VideoPlay /></el-icon>
            {{ kernelStatus === 'running' ? '停止内核' : '启动内核' }}
          </el-button>
          <el-button @click="addCell('code')" :disabled="kernelStatus !== 'running'">
            <el-icon><Plus /></el-icon>
            代码单元格
          </el-button>
          <el-button @click="addCell('markdown')" :disabled="kernelStatus !== 'running'">
            <el-icon><Document /></el-icon>
            Markdown
          </el-button>
        </el-button-group>
      </div>
      
      <div class="toolbar-right">
        <el-dropdown>
          <el-button>
            <el-icon><More /></el-icon>
            更多
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="saveNotebook">
                <el-icon><DocumentChecked /></el-icon>
                <span>保存 Notebook</span>
              </el-dropdown-item>
              <el-dropdown-item @click="exportNotebook">
                <el-icon><Download /></el-icon>
                <span>导出 .ipynb</span>
              </el-dropdown-item>
              <el-dropdown-item @click="clearAllOutputs">
                <el-icon><Delete /></el-icon>
                <span>清空所有输出</span>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
    
    <!-- 变量面板 -->
    <el-drawer v-model="showVariables" direction="rtl" title="变量查看器" size="350px">
      <div class="variables-panel">
        <el-input v-model="variableFilter" placeholder="搜索变量" size="small" class="mb-10" clearable />
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
    <div class="cells-container">
      <div v-for="(cell, index) in cells" :key="cell.id" class="code-cell">
        <!-- 单元格工具栏 -->
        <div class="cell-toolbar">
          <div class="cell-type-badge" :class="cell.cell_type">
            {{ cell.cell_type === 'code' ? 'Code' : 'MD' }}
          </div>
          <div class="cell-execution" v-if="cell.cell_type === 'code'">
            <span v-if="cell.execution_count !== null" class="execution-count">[{{ cell.execution_count }}]</span>
            <el-tag v-else size="small" effect="light">未执行</el-tag>
          </div>
          <div class="cell-actions">
            <el-button size="small" @click="moveCell(index, -1)" :disabled="index === 0">
              <el-icon><ArrowUp /></el-icon>
            </el-button>
            <el-button size="small" @click="moveCell(index, 1)" :disabled="index === cells.length - 1">
              <el-icon><ArrowDown /></el-icon>
            </el-button>
            <el-button size="small" @click="duplicateCell(index)">
              <el-icon><DocumentCopy /></el-icon>
            </el-button>
            <el-button size="small" type="danger" @click="deleteCell(index)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
        
        <!-- 代码单元格编辑器 -->
        <div v-if="cell.cell_type === 'code'" class="cell-editor">
          <div ref="editorContainer" class="monaco-editor"></div>
          
          <!-- 执行按钮 -->
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
                  <img v-else-if="output.data['image/png']" :src="'data:image/png;base64,' + output.data['image/png']" />
                </div>
                <div v-else-if="output.output_type === 'display_data'" class="display-data">
                  <div v-if="output.data['text/html']" v-html="output.data['text/html']" />
                  <img v-else-if="output.data['image/png']" :src="'data:image/png;base64,' + output.data['image/png']" />
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
        
        <!-- Markdown 单元格 -->
        <div v-else class="markdown-cell">
          <div v-if="editingCells.includes(index)" class="markdown-editor">
            <textarea v-model="cell.source" @blur="saveCell(index)" @keydown.esc="cancelEdit(index)" rows="10" spellcheck="false" />
            <div class="editor-actions">
              <el-button size="small" @click="saveCell(index)">保存</el-button>
              <el-button size="small" type="info" @click="cancelEdit(index)">取消</el-button>
            </div>
          </div>
          <div v-else class="markdown-preview" v-html="renderedMarkdown(cell.source)" @dblclick="startEdit(index)" />
        </div>
      </div>
      
      <!-- 底部添加按钮 -->
      <div class="add-cell-bottom" @click="addCell('code')">
        <el-icon><Plus /></el-icon>
        <span>添加代码单元格</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { marked } from 'marked'
import {
  ArrowLeft, VideoPlay, VideoPause, Plus, Document, More, DocumentChecked, Download, Delete,
  ArrowUp, ArrowDown, DocumentCopy, Notebook
} from '@element-plus/icons-vue'

// 动态导入 Monaco Editor
let monaco: any = null
const loadMonaco = async () => {
  if (!monaco) {
    const mod = await import('monaco-editor')
    monaco = mod.default
    // 配置主题
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


const getStatusType = (status?: string) => {
  const map: Record<string, string> = {
    starting: 'info', running: 'success', idle: 'warning',
    busy: 'primary', stopped: 'info', error: 'danger',
  }
  return (map[status || ''] || 'info') as any
}

const getStatusText = (status?: string) => {
  const map: Record<string, string> = {
    starting: '启动中', running: '运行中', idle: '空闲',
    busy: '忙碌', stopped: '已停止', error: '错误',
  }
  return map[status || ''] || (status || '-')
}

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const sessionId = route.params.id as string
const session = ref<any>(null)
const cells = ref<any[]>([])
const kernelStatus = ref('stopped')
const kernelLoading = ref(false)
const executingCells = ref<number[]>([])
const editingCells = ref<number[]>([])
const showVariables = ref(false)
const variables = ref<Record<string, any>>({})
const variableFilter = ref('')
const editors = ref<Map<number, any>>(new Map())

const filteredVariables = computed(() => {
  if (!variableFilter.value) return variables.value
  const filter = variableFilter.value.toLowerCase()
  const result: Record<string, any> = {}
  for (const [k, v] of Object.entries(variables.value)) {
    if (k.toLowerCase().includes(filter)) result[k] = v
  }
  return result
})

const fetchSession = async () => {
  try {
    const res = await api.get(`/workbench/sessions/${sessionId}`)
    session.value = res
    kernelStatus.value = res.status
    cells.value = res.cells || []
    // 恢复编辑器内容
    await nextTick()
    initializeEditors()
  } catch (err) {
    appStore.error('加载失败', err.message)
    router.push('/workbench/sessions')
  }
}

const fetchVariables = async () => {
  try {
    const res = await api.get(`/workbench/sessions/${sessionId}/variables`)
    variables.value = res.variables || {}
  } catch (err) {
    console.error('获取变量失败', err)
  }
}

const initializeEditors = async () => {
  await loadMonaco()
  for (let i = 0; i < cells.value.length; i++) {
    if (cells.value[i].cell_type === 'code') {
      createEditor(i)
    }
  }
}

const createEditor = (index: number) => {
  const container = document.querySelector(`.cell-editor:nth-of-type(${index + 1}) .monaco-editor`)
  if (!container || editors.value.has(index)) return
  
  const editor = monaco.editor.create(container, {
    value: cells.value[index].source,
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
    cells.value[index].source = editor.getValue()
  })
  
  editors.value.set(index, editor)
}

const destroyEditor = (index: number) => {
  const editor = editors.value.get(index)
  if (editor) {
    editor.dispose()
    editors.value.delete(index)
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
  } catch (err) {
    appStore.error('操作失败', err.message)
  } finally {
    kernelLoading.value = false
  }
}

const addCell = (type: 'code' | 'markdown', afterIndex?: number) => {
  const newCell = {
    id: `cell-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
    cell_type: type,
    source: type === 'code' ? '' : '',
    outputs: [],
    execution_count: null,
    metadata: {},
  }
  
  const insertIndex = afterIndex !== undefined ? afterIndex + 1 : cells.value.length
  cells.value.splice(insertIndex, 0, newCell)
  
  nextTick(() => {
    if (type === 'code') {
      createEditor(insertIndex)
    }
  })
}

const executeCell = async (index: number) => {
  if (kernelStatus.value !== 'running') {
    appStore.warning('请先启动内核')
    return
  }
  
  const cell = cells.value[index]
  if (!cell.source.trim()) return
  
  executingCells.value.push(index)
  
  try {
    const result = await api.post(`/workbench/sessions/${sessionId}/cells/execute`, {
      cell_id: cell.id,
      cell_index: index,
      code: cell.source,
      cell_type: 'code',
      mode: 'single',
    })
    
    // 更新单元格
    cell.outputs = result.outputs || []
    cell.execution_count = result.execution_count
    cell.status = result.success ? 'completed' : 'error'
    if (result.error) cell.error = result.error
    
    // 更新变量
    if (result.variables) {
      variables.value = { ...variables.value, ...result.variables }
    }
    
    if (!result.success) {
      appStore.error('执行失败', result.error)
    }
  } catch (err) {
    appStore.error('执行失败', err.message)
    cell.outputs = [{ output_type: 'error', ename: 'Error', evalue: err.message, traceback: [err.message] }]
  } finally {
    executingCells.value = executingCells.value.filter(i => i !== index)
  }
}

const interruptCell = async (index: number) => {
  try {
    await api.post(`/workbench/sessions/${sessionId}/cells/interrupt`)
    executingCells.value = executingCells.value.filter(i => i !== index)
    appStore.success('已中断')
  } catch (err) {
    appStore.error('中断失败', err.message)
  }
}

const saveCell = async (index: number) => {
  const cell = cells.value[index]
  try {
    await api.put(`/workbench/sessions/${sessionId}/cells`, {
      cell_id: cell.id,
      source: cell.source,
      cell_type: cell.cell_type,
      cell_index: index,
    })
    editingCells.value = editingCells.value.filter(i => i !== index)
    appStore.success('保存成功')
  } catch (err) {
    appStore.error('保存失败', err.message)
  }
}

const startEdit = (index: number) => {
  if (cells.value[index].cell_type === 'markdown') {
    editingCells.value.push(index)
  }
}

const cancelEdit = (index: number) => {
  editingCells.value = editingCells.value.filter(i => i !== index)
}

const deleteCell = (index: number) => {
  if (cells.value[index].cell_type === 'code') {
    destroyEditor(index)
  }
  cells.value.splice(index, 1)
}

const duplicateCell = (index: number) => {
  const cell = { ...cells.value[index], id: `cell-${Date.now()}` }
  cells.value.splice(index + 1, 0, cell)
  nextTick(() => {
    if (cell.cell_type === 'code') {
      createEditor(index + 1)
    }
  })
}

const moveCell = (index: number, direction: number) => {
  const newIndex = index + direction
  if (newIndex < 0 || newIndex >= cells.value.length) return
  ;[cells.value[index], cells.value[newIndex]] = [cells.value[newIndex], cells.value[index]]
  
  // 重建编辑器
  editors.value.clear()
  nextTick(initializeEditors)
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

const saveNotebook = async () => {
  try {
    await api.put(`/workbench/sessions/${sessionId}/notebook`, {
      cells: cells.value.map((c, i) => ({
        cell_type: c.cell_type,
        source: c.source,
        outputs: c.outputs,
        execution_count: c.execution_count,
        metadata: c.metadata,
      })),
    })
    appStore.success('Notebook 保存成功')
  } catch (err) {
    appStore.error('保存失败', err.message)
  }
}

const exportNotebook = async () => {
  try {
    const content = {
      nbformat: 4,
      nbformat_minor: 4,
      metadata: { kernelspec: { name: 'python3', display_name: 'Python 3' } },
      cells: cells.value.map(c => ({
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
  } catch (err) {
    appStore.error('导出失败', err.message)
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

const getTypeLabel = (value: any) => {
  if (value === null) return 'null'
  if (Array.isArray(value)) return `Array[${value.length}]`
  if (typeof value === 'object') return `Object{${Object.keys(value).length}}`
  return typeof value
}

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

const renderedMarkdown = (source: string) => {
  return marked.parse(source || '')
}

const goBack = () => router.push('/workbench/sessions')

onMounted(async () => {
  await loadMonaco()
  await fetchSession()
  await fetchVariables()
  
  // 定期刷新变量
  const interval = setInterval(fetchVariables, 5000)
  onUnmounted(() => clearInterval(interval))
  
  // 清理编辑器
  onUnmounted(() => {
    editors.value.forEach(editor => editor.dispose())
    editors.value.clear()
  })
})
</script>

<style scoped>
.workbench-session { display: flex; flex-direction: column; height: calc(100vh - 120px); }
.session-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 12px 20px; background: #fff; border-bottom: 1px solid #ebeef5; flex-wrap: wrap; gap: 16px; }
.toolbar-left { display: flex; align-items: center; gap: 12px; }
.back-btn { margin-right: 8px; }
.session-title { font-size: 18px; font-weight: 500; margin: 0; }
.toolbar-center { display: flex; gap: 8px; }
.toolbar-right { display: flex; gap: 8px; }
.variables-panel { padding: 8px; }
.variable-list { max-height: 60vh; overflow: auto; }
.variable-item { display: flex; align-items: center; padding: 8px 12px; border-bottom: 1px solid #ebeef5; font-size: 13px; gap: 12px; }
.variable-name { font-weight: 500; min-width: 120px; }
.variable-type { color: #909399; font-size: 11px; background: #f5f7fa; padding: 1px 6px; border-radius: 3px; }
.variable-value { margin-left: auto; font-family: monospace; font-size: 11px; color: #606266; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-variables { text-align: center; padding: 40px; color: #909399; }
.cells-container { flex: 1; overflow: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
.code-cell { border: 1px solid #ebeef5; border-radius: 8px; background: #fff; overflow: hidden; }
.cell-toolbar { display: flex; align-items: center; padding: 8px 12px; background: #f5f7fa; border-bottom: 1px solid #ebeef5; gap: 12px; }
.cell-type-badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 3px; min-width: 44px; text-align: center; }
.cell-type-badge.code { background: #ecf5ff; color: #409eff; }
.cell-type-badge.markdown { background: #f0f9eb; color: #67c23a; }
.cell-execution { display: flex; align-items: center; gap: 8px; }
.execution-count { font-family: monospace; font-size: 12px; color: #909399; }
.cell-actions { margin-left: auto; display: flex; gap: 4px; }
.cell-editor { position: relative; min-height: 120px; }
.cell-execute-bar { display: flex; gap: 8px; padding: 8px 12px; background: #fafafa; border-top: 1px solid #ebeef5; }
.cell-output { border-top: 1px solid #ebeef5; background: #1e1e1e; }
.output-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: #2d2d2d; color: #ccc; font-size: 12px; }
.output-content { padding: 12px; max-height: 400px; overflow: auto; font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; line-height: 1.6; }
.output-item { margin-bottom: 8px; }
.stream-output { color: #85e89d; white-space: pre-wrap; word-break: break-word; }
.stream-name { color: #9cdcfe; }
.execute-result { color: #d4d4d4; }
.error-output { color: #f14c4c; }
.error-name { font-weight: bold; margin-bottom: 4px; }
.error-traceback { font-size: 11px; white-space: pre-wrap; }
.markdown-cell { border: 1px solid #ebeef5; border-radius: 8px; background: #fff; }
.markdown-editor { padding: 12px; border-bottom: 1px solid #ebeef5; }
.editor-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.markdown-preview { padding: 16px; min-height: 60px; cursor: pointer; }
.markdown-preview:hover { background: #fafafa; }
.markdown-preview h1, .markdown-preview h2, .markdown-preview h3 { margin-top: 16px; margin-bottom: 8px; }
.markdown-preview code { background: #f5f5f5; padding: 2px 4px; border-radius: 3px; font-family: monospace; }
.markdown-preview pre { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 4px; overflow: auto; }
.markdown-preview pre code { background: transparent; padding: 0; }
.add-cell-bottom { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 20px; color: #909399; border: 2px dashed #dcdfe6; border-radius: 8px; margin-top: 16px; cursor: pointer; transition: all 0.2s; }
.add-cell-bottom:hover { border-color: #409eff; color: #409eff; background: #ecf5ff; }
.mb-10 { margin-bottom: 10px; }
</style>