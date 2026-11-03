<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">训练任务列表</h1>
      <div class="header-actions">
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 140px" @change="refreshData">
          <el-option label="排队中" value="queued" />
          <el-option label="运行中" value="running" />
          <el-option label="已暂停" value="paused" />
          <el-option label="已完成" value="completed" />
          <el-option label="已失败" value="failed" />
          <el-option label="已取消" value="cancelled" />
        </el-select>
        <el-button @click="refreshData" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="router.push('/training/create')">
          <el-icon><Plus /></el-icon>
          创建任务
        </el-button>
      </div>
    </div>
    <el-card>
      <div v-if="loading" class="loading-placeholder">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <p>加载中...</p>
      </div>
      <div v-else-if="error" class="empty-state">
        <el-icon class="empty-state-icon" :size="48"><WarningFilled /></el-icon>
        <p>{{ error }}</p>
        <el-button type="primary" @click="refreshData">重试</el-button>
      </div>
      <template v-else>
        <el-alert v-if="filteredJobs.length === 0" :title="jobs.length === 0 ? '暂无训练任务，点击右上角创建' : '当前筛选条件下没有任务'" type="info" :closable="false" center />
        <el-table v-else :data="filteredJobs" border size="small" style="width: 100%">
          <el-table-column prop="id" label="任务 ID" width="200" show-overflow-tooltip>
            <template #default="scope">
              <el-link type="primary" @click="viewJob(scope.row.id)">{{ scope.row.id }}</el-link>
            </template>
          </el-table-column>
          <el-table-column label="任务类型" width="150">
            <template #default="scope">
              <el-tag size="small" :type="getTaskTypeTag(specOf(scope.row).task) as any">
                {{ taskText(specOf(scope.row).task) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="数据集" min-width="140" show-overflow-tooltip>
            <template #default="scope">{{ specOf(scope.row).dataset_name || specOf(scope.row).dataset_id || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <el-tag :type="getStatusTag(scope.row.status) as any" effect="dark" size="small">
                {{ getStatusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="设备" width="110">
            <template #default="scope">{{ scope.row.device || resourceText(scope.row) }}</template>
          </el-table-column>
          <el-table-column label="创建时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="scope">
              <el-button size="small" link type="primary" @click="viewJob(scope.row.id)">详情</el-button>
              <el-button
                v-if="scope.row.status === 'running' || scope.row.status === 'queued'"
                size="small" link type="warning" @click="cancelJob(scope.row.id)">取消</el-button>
              <el-button
                v-if="scope.row.status === 'failed' || scope.row.status === 'cancelled' || scope.row.status === 'completed' || scope.row.status === 'paused'"
                size="small" link type="danger" @click="deleteJob(scope.row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading, WarningFilled, Plus } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const jobs = ref<any[]>([])
const loading = ref(false)
const error = ref('')
const statusFilter = ref('')

// 后端返回的任务视图把任务类型/数据集放在 spec 内，这里统一取出
const specOf = (row: any) => row?.spec || {}

const filteredJobs = computed(() => {
  if (!statusFilter.value) return jobs.value
  return jobs.value.filter((job) => job.status === statusFilter.value)
})

// 后端实际路径为 /api/training/jobs（旧版误请求 /api/jobs 导致 502/404）
const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/training/jobs')
    jobs.value = Array.isArray(res) ? res : (res.jobs || [])
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const viewJob = (id: string) => router.push(`/training/detail/${id}`)

const cancelJob = async (id: string) => {
  try {
    await appStoreConfirm(`确认取消任务 ${id}？正在运行的任务会在当前批次结束后停止。`)
    await api.post(`/training/jobs/${id}/cancel`)
    appStore.success('任务已取消')
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('取消失败', err?.message)
  }
}

const deleteJob = async (id: string) => {
  try {
    await appStoreConfirm(`确认删除任务 ${id}？删除后任务记录与日志不可恢复。`)
    await api.delete(`/training/jobs/${id}`)
    appStore.success('任务已删除')
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('删除失败', err?.message)
  }
}

// 使用 Element Plus MessageBox 作为确认弹窗
import { ElMessageBox } from 'element-plus'
const appStoreConfirm = (message: string) =>
  ElMessageBox.confirm(message, '操作确认', { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' })

const TASK_TEXT: Record<string, string> = {
  classification: '分类',
  regression: '回归',
  clustering: '聚类',
  time_series_forecasting: '时间序列预测',
  llm_pretraining: 'LLM 预训练',
  llm_sft: 'LLM 微调',
  llm_dpo: 'LLM DPO',
  llm_ppo: 'LLM PPO',
}
const taskText = (task?: string) => TASK_TEXT[task || ''] || task || '-'
const getTaskTypeTag = (task?: string) => {
  const map: Record<string, string> = {
    classification: 'primary', regression: 'success', clustering: 'warning',
    time_series_forecasting: 'info', llm_pretraining: 'danger', llm_sft: 'danger',
    llm_dpo: 'danger', llm_ppo: 'danger',
  }
  return map[task || ''] || 'info'
}

const STATUS_TEXT: Record<string, string> = {
  queued: '排队中', running: '运行中', paused: '已暂停',
  completed: '已完成', failed: '失败', cancelled: '已取消',
}
const getStatusText = (status?: string) => STATUS_TEXT[status || ''] || status || '-'
const getStatusTag = (status?: string) => {
  const map: Record<string, string> = {
    queued: 'info', running: 'primary', paused: 'warning',
    completed: 'success', failed: 'danger', cancelled: 'info',
  }
  return map[status || ''] || 'info'
}

const resourceText = (row: any) => {
  const hint = row?.resource_hint || {}
  if (hint.parallel_workers) return `联合训练 ×${hint.parallel_workers}`
  return hint.require_gpu ? 'GPU' : '自动'
}

const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(refreshData)
</script>

<style scoped>
.page-view { padding: 0; }
.header-actions { display: flex; align-items: center; gap: 12px; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
