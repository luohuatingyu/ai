<template>
  <div class="workbench-sessions">
    <div class="page-header">
      <h1 class="page-title">交互式工作台</h1>
      <el-button type="primary" @click="router.push('/workbench/new')">
        <el-icon><Plus /></el-icon>
        新建会话
      </el-button>
    </div>
    
    <!-- 会话卡片网格 -->
    <el-row :gutter="20" v-if="sessions.length > 0">
      <el-col :xs="24" :sm="12" :md="8" :lg="6" v-for="session in sessions" :key="session.id">
        <el-card class="session-card" :class="{ 'session-running': session.status === 'running' }">
          <div class="session-header">
            <h3 class="session-name">{{ session.name }}</h3>
            <el-tag :type="getStatusType(session.status) as any" effect="dark" size="small">
              {{ getStatusText(session.status) }}
            </el-tag>
          </div>
          
          <div class="session-meta">
            <el-descriptions :column="1" border size="small">
              <el-descriptions-item label="ID">{{ session.id }}</el-descriptions-item>
              <el-descriptions-item label="内核">{{ session.kernel_type }}</el-descriptions-item>
              <el-descriptions-item label="绑定资源">{{ session.bound_workers?.length || 0 }} 个 Worker</el-descriptions-item>
              <el-descriptions-item label="最后活动">{{ formatDateTime(session.last_activity) }}</el-descriptions-item>
            </el-descriptions>
          </div>
          
          <div class="session-actions">
            <el-button 
              size="small" 
              :type="session.status === 'running' ? 'success' : 'primary'" 
              :loading="kernelLoading[session.id]"
              @click="toggleKernel(session)"
            >
              <el-icon v-if="session.status === 'running'"><VideoPause /></el-icon>
              <el-icon v-else><VideoPlay /></el-icon>
              {{ session.status === 'running' ? '停止内核' : '启动内核' }}
            </el-button>
            <el-button size="small" @click="openSession(session.id)">
              <el-icon><Edit /></el-icon>
              打开工作台
            </el-button>
            <el-dropdown>
              <el-button size="small">
                <el-icon><More /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="duplicateSession(session)">
                    <el-icon><DocumentCopy /></el-icon>
                    <span>复制会话</span>
                  </el-dropdown-item>
                  <el-dropdown-item @click="exportSession(session)">
                    <el-icon><Download /></el-icon>
                    <span>导出 Notebook</span>
                  </el-dropdown-item>
                  <el-dropdown-item divided @click="deleteSession(session.id)">
                    <el-icon><Delete /></el-icon>
                    <span style="color: #f56c6c">删除</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </el-card>
      </el-col>
    </el-row>
    
    <!-- 空状态 -->
    <div v-else class="empty-state">
      <el-icon class="empty-state-icon"><Notebook /></el-icon>
      <p>暂无会话</p>
      <el-button type="primary" @click="router.push('/workbench/new')">
        <el-icon><Plus /></el-icon>
        创建第一个会话
      </el-button>
    </div>
    
    <!-- 新建会话对话框 -->
    <el-dialog v-model="showCreateDialog" title="新建 Notebook 会话" width="600px">
      <el-form :model="createForm" ref="createFormRef" label-width="100px">
        <el-form-item label="会话名称" prop="name">
          <el-input v-model="createForm.name" placeholder="输入会话名称" />
        </el-form-item>
        <el-form-item label="关联数据集" prop="dataset_id">
          <el-select v-model="createForm.dataset_id" placeholder="选择数据集（可选）" style="width: 100%">
            <el-option v-for="ds in availableDatasets" :key="ds.id" :label="ds.name" :value="ds.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="内核类型" prop="kernel_type">
          <el-radio-group v-model="createForm.kernel_type">
            <el-radio value="python">Python</el-radio>
            <el-radio value="bash">Bash</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="绑定计算资源" prop="bound_workers">
          <el-checkbox-group v-model="createForm.bound_workers">
            <el-checkbox v-for="w in availableWorkers" :key="w.worker_id" :label="w.worker_id" :value="w.worker_id">
              {{ w.worker_id }} ({{ w.device_label || w.device }})
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="环境配置" prop="metadata">
          <el-input v-model="createForm.metadata_json" type="textarea" :rows="4" placeholder="JSON 格式环境变量/依赖配置" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import {
  Plus, VideoPlay, VideoPause, Edit, More, Download, Delete,
  DocumentCopy, Notebook, Cpu, ArrowRight
} from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

const sessions = ref<any[]>([])
const loading = ref(false)
const kernelLoading = ref<Record<string, boolean>>({})
const availableDatasets = ref<any[]>([])
const availableWorkers = ref<any[]>([])

// 新建对话框
const showCreateDialog = ref(false)
const creating = ref(false)
const createForm = reactive({
  name: '',
  dataset_id: '',
  kernel_type: 'python',
  bound_workers: [] as string[],
  metadata_json: '{}',
})
const createFormRef = ref()

const fetchSessions = async () => {
  loading.value = true
  try {
    const res = await api.get('/workbench/sessions', { limit: 100 })
    sessions.value = res.sessions || []
  } catch (err) {
    appStore.error('加载失败', err.message)
  } finally {
    loading.value = false
  }
}

const fetchResources = async () => {
  try {
    const [datasetsRes, workersRes] = await Promise.all([
      api.get('/datasets', { limit: 1000 }),
      api.get('/clusters/workers/summary'),
    ])
    availableDatasets.value = datasetsRes.datasets || []
    availableWorkers.value = workersRes.details || []
  } catch (err) {
    console.error('获取资源失败', err)
  }
}

const getStatusType = (status: string) => {
  const map: Record<string, string> = {
    starting: 'info',
    running: 'success',
    idle: 'warning',
    busy: 'primary',
    stopped: 'info',
    error: 'danger',
  }
  return map[status] || 'info'
}

const getStatusText = (status: string) => {
  const map: Record<string, string> = {
    starting: '启动中',
    running: '运行中',
    idle: '空闲',
    busy: '忙碌',
    stopped: '已停止',
    error: '错误',
  }
  return map[status] || status
}

const formatDateTime = (ts: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

const toggleKernel = async (session: any) => {
  kernelLoading.value[session.id] = true
  try {
    if (session.status === 'running') {
      await api.post(`/workbench/sessions/${session.id}/kernel/stop`)
      appStore.success('内核已停止')
    } else {
      await api.post(`/workbench/sessions/${session.id}/kernel/start`)
      appStore.success('内核已启动')
    }
    fetchSessions()
  } catch (err) {
    appStore.error('操作失败', err.message)
  } finally {
    kernelLoading.value[session.id] = false
  }
}

const openSession = (id: string) => {
  router.push(`/workbench/session/${id}`)
}

const submitCreate = async () => {
  creating.value = true
  try {
    let metadata: Record<string, any> = {}
    if (createForm.metadata_json.trim()) {
      metadata = JSON.parse(createForm.metadata_json)
    }
    await api.post('/workbench/sessions', {
      name: createForm.name,
      dataset_id: createForm.dataset_id || null,
      kernel_type: createForm.kernel_type,
      bound_workers: createForm.bound_workers,
      metadata,
    })
    appStore.success('会话创建成功')
    showCreateDialog.value = false
    createForm.name = ''
    createForm.dataset_id = ''
    createForm.kernel_type = 'python'
    createForm.bound_workers = []
    createForm.metadata_json = '{}'
    fetchSessions()
  } catch (err) {
    appStore.error('创建失败', err.message)
  } finally {
    creating.value = false
  }
}

const duplicateSession = async (session: any) => {
  try {
    await api.post('/workbench/sessions', {
      name: `${session.name} (副本)`,
      dataset_id: session.dataset_id,
      kernel_type: session.kernel_type,
      bound_workers: session.bound_workers,
      metadata: session.metadata,
    })
    appStore.success('会话已复制')
    fetchSessions()
  } catch (err) {
    appStore.error('复制失败', err.message)
  }
}

const exportSession = async (session: any) => {
  try {
    const cells = await api.get(`/workbench/sessions/${session.id}/cells`)
    const content = {
      nbformat: 4,
      nbformat_minor: 4,
      metadata: { kernelspec: { name: 'python3', display_name: 'Python 3' } },
      cells: cells.map((c: any) => ({
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
    a.download = `${session.name}.ipynb`
    a.click()
    URL.revokeObjectURL(url)
    appStore.success('导出成功')
  } catch (err) {
    appStore.error('导出失败', err.message)
  }
}

const deleteSession = async (id: string) => {
  try {
    await ElMessageBox.confirm(
      `确认删除会话 ${id}？会话内的单元格与变量将一并清理。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await api.delete(`/workbench/sessions/${id}`, { params: { force: "true" } })
    appStore.success('删除成功')
    fetchSessions()
  } catch (err) {
    appStore.error('删除失败', err.message)
  }
}

onMounted(() => {
  fetchSessions()
  fetchResources()
})
</script>

<style scoped>
.workbench-sessions { padding: 0; }
.session-card { transition: all 0.3s; }
.session-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
.session-running { border-left: 4px solid #67c23a; }
.session-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.session-name { font-size: 16px; font-weight: 500; color: #303133; margin: 0; }
.session-meta { margin: 16px 0; }
.session-actions { display: flex; gap: 8px; justify-content: flex-end; }
.empty-state { text-align: center; padding: 60px 20px; color: #909399; }
.empty-state-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.5; }
.empty-state p { margin-bottom: 20px; font-size: 16px; }
</style>