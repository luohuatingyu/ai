<template>
  <div class="page-view">
    <div class="page-header">
      <div class="header-left">
        <el-button @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <h1 class="page-title">任务详情</h1>
        <el-tag v-if="detail" :type="getStatusTag(detail.status) as any" effect="dark">
          {{ getStatusText(detail.status) }}
        </el-tag>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="fetchDetail">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button
          v-if="detail && ['running', 'queued'].includes(detail.status)"
          type="warning" @click="cancelJob">取消任务</el-button>
        <el-button
          v-if="detail && detail.status === 'completed'"
          type="success" @click="downloadModel">
          <el-icon><Download /></el-icon>
          下载模型
        </el-button>
      </div>
    </div>

    <div v-if="error" class="empty-state">
      <el-icon class="empty-state-icon" :size="48"><WarningFilled /></el-icon>
      <p>{{ error }}</p>
      <el-button type="primary" @click="fetchDetail">重试</el-button>
    </div>

    <template v-else>
      <el-card class="mb-20">
        <template #header><span>基本信息</span></template>
        <el-descriptions :column="2" border v-if="detail">
          <el-descriptions-item label="任务 ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="任务类型">{{ taskText(spec.task) }}</el-descriptions-item>
          <el-descriptions-item label="数据集">{{ spec.dataset_name || spec.dataset_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="目标列">{{ spec.target || '-' }}</el-descriptions-item>
          <el-descriptions-item label="设备">{{ detail.device || '自动分配' }}</el-descriptions-item>
          <el-descriptions-item label="计算槽">{{ detail.worker_id || detail.machine_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatDate(detail.finished_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.last_error || detail.error" label="错误信息" :span="2">
            <span class="error-text">{{ detail.last_error || detail.error }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <p>加载中...</p>
        </div>
      </el-card>

      <el-card class="mb-20" v-if="detail">
        <template #header><span>训练进度</span></template>
        <el-progress
          :percentage="Math.min(100, Math.round(progress.percent || 0))"
          :status="detail.status === 'failed' ? 'exception' : (progress.percent >= 100 ? 'success' : undefined)"
        />
        <p class="progress-text">Epoch {{ progress.epoch }} / {{ progress.epochs_total }}</p>
      </el-card>

      <el-card class="mb-20" v-if="detail && metrics.length > 0">
        <template #header><span>训练指标</span></template>
        <el-table :data="metrics" border size="small" max-height="320">
          <el-table-column prop="epoch" label="Epoch" width="90" />
          <el-table-column v-for="key in metricKeys" :key="key" :prop="key" :label="key" />
        </el-table>
      </el-card>

      <el-card v-if="detail">
        <template #header>
          <div class="card-header">
            <span>训练日志</span>
            <el-button size="small" @click="fetchLogs">加载日志</el-button>
          </div>
        </template>
        <div class="log-panel">
          <div v-if="logs.length === 0" class="log-empty">暂无日志，训练启动后自动产出</div>
          <pre v-else class="log-content">{{ logs.join('\n') }}</pre>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { ArrowLeft, Loading, Refresh, Download, WarningFilled } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const detail = ref<any>(null)
const loading = ref(false)
const error = ref('')
const logs = ref<string[]>([])

const jobId = String(route.params.id || '')
const spec = computed(() => detail.value?.spec || {})
const metrics = computed<any[]>(() => detail.value?.metrics || [])
const metricKeys = computed(() => {
  const keys = new Set<string>()
  for (const m of metrics.value) {
    Object.keys(m || {}).forEach((k) => { if (k !== 'epoch') keys.add(k) })
  }
  return Array.from(keys)
})
const progress = computed(() => detail.value?.progress || { epoch: 0, epochs_total: 0, percent: 0 })

// 后端实际路径为 /api/training/jobs/{id}（旧版误请求 /api/jobs/{id}）
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/training/jobs/${jobId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const fetchLogs = async () => {
  try {
    const res: any = await api.get(`/training/jobs/${jobId}/logs`, { offset: 0 })
    logs.value = res.lines || []
  } catch (err: any) {
    appStore.error('日志加载失败', err?.message)
  }
}

const cancelJob = async () => {
  try {
    await api.post(`/training/jobs/${jobId}/cancel`)
    appStore.success('已发送取消请求')
    fetchDetail()
  } catch (err: any) {
    appStore.error('取消失败', err?.message)
  }
}

const downloadModel = () => {
  // 模型文件下载走后端文件流接口
  window.open(`/api/training/jobs/${jobId}/model`, '_blank')
}

const goBack = () => window.history.length > 1 ? router.back() : router.replace('/training/jobs')

const TASK_TEXT: Record<string, string> = {
  classification: '分类', regression: '回归', clustering: '聚类',
  time_series_forecasting: '时间序列预测', llm_pretraining: 'LLM 预训练',
  llm_sft: 'LLM 微调', llm_dpo: 'LLM DPO', llm_ppo: 'LLM PPO',
}
const taskText = (task?: string) => TASK_TEXT[task || ''] || task || '-'
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
const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

onMounted(fetchDetail)
</script>

<style scoped>
.page-view { padding: 0; }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-actions { display: flex; gap: 12px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
.progress-text { margin-top: 8px; color: #909399; font-size: 13px; }
.error-text { color: #f56c6c; word-break: break-all; }
.log-panel { max-height: 360px; overflow: auto; background: #1e1e1e; border-radius: 6px; }
.log-content {
  margin: 0; padding: 12px; color: #d4d4d4;
  font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}
.log-empty { text-align: center; padding: 32px; color: #909399; }
.mb-20 { margin-bottom: 20px; }
</style>
