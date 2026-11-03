<template>
  <div class="page-view">
    <div class="page-header">
      <div class="header-left">
        <el-button @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <h1 class="page-title">执行详情</h1>
        <el-tag v-if="detail" :type="statusTag(detail.status) as any" effect="dark">
          {{ statusText(detail.status) }}
        </el-tag>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="fetchDetail">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button
          v-if="detail && ['pending', 'running'].includes(detail.status)"
          type="warning" @click="cancelRun">取消执行</el-button>
      </div>
    </div>

    <div v-if="error" class="empty-state">
      <el-icon class="empty-state-icon" :size="48"><WarningFilled /></el-icon>
      <p>{{ error }}</p>
      <el-button type="primary" @click="fetchDetail">重试</el-button>
    </div>

    <template v-else>
      <el-card class="mb-20" v-loading="loading">
        <template #header><span>执行概要</span></template>
        <el-descriptions :column="2" border v-if="detail">
          <el-descriptions-item label="执行 ID">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="触发方式">{{ triggerText(detail.trigger) }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ formatDate(detail.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatDate(detail.finished_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.error" label="错误信息" :span="2">
            <span class="error-text">{{ detail.error }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <p>加载中...</p>
        </div>
      </el-card>

      <el-card v-if="detail">
        <template #header><span>步骤执行情况</span></template>
        <el-alert v-if="stepRuns.length === 0" title="暂无步骤执行记录" type="info" :closable="false" center />
        <el-table v-else :data="stepRuns" border size="small">
          <el-table-column prop="step_name" label="步骤" min-width="140" />
          <el-table-column label="类型" width="120">
            <template #default="scope">
              <el-tag size="small">{{ scope.row.step_type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <el-tag size="small" :type="statusTag(scope.row.status) as any" effect="dark">
                {{ statusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="worker_id" label="执行槽" min-width="130">
            <template #default="scope">{{ scope.row.worker_id || '-' }}</template>
          </el-table-column>
          <el-table-column label="开始时间" width="150">
            <template #default="scope">{{ formatDate(scope.row.started_at) }}</template>
          </el-table-column>
          <el-table-column label="结束时间" width="150">
            <template #default="scope">{{ formatDate(scope.row.finished_at) }}</template>
          </el-table-column>
          <el-table-column label="错误" min-width="160" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.error || '-' }}</template>
          </el-table-column>
        </el-table>

        <div v-if="runLogs" class="log-panel">
          <pre>{{ runLogs }}</pre>
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
import { ArrowLeft, Loading, Refresh, WarningFilled } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const detail = ref<any>(null)
const loading = ref(false)
const error = ref('')

const pipelineId = String(route.params.pipelineId || '')
const runId = String(route.params.runId || '')

const stepRuns = computed<any[]>(() => detail.value?.steps || [])
const runLogs = computed(() => {
  const logs = stepRuns.value
    .flatMap((s) => s.logs || [])
    .filter(Boolean)
  return logs.join('\n')
})

// 路由携带 pipelineId + runId：/pipelines/run/{pipelineId}/{runId}
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/pipelines/${pipelineId}/runs/${runId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const cancelRun = async () => {
  try {
    await api.post(`/pipelines/${pipelineId}/runs/${runId}/cancel`)
    appStore.success('已发送取消请求')
    fetchDetail()
  } catch (err: any) {
    appStore.error('取消失败', err?.message)
  }
}

const TRIGGER_TEXT: Record<string, string> = {
  manual: '手动', scheduled: '定时', event: '事件', api: 'API',
}
const triggerText = (trigger?: string) => TRIGGER_TEXT[trigger || ''] || trigger || '-'

const STATUS_TEXT: Record<string, string> = {
  pending: '等待中', running: '运行中', completed: '已完成',
  failed: '失败', cancelled: '已取消', skipped: '已跳过',
}
const statusText = (status?: string) => STATUS_TEXT[status || ''] || status || '-'
const statusTag = (status?: string) => {
  const map: Record<string, string> = {
    pending: 'info', running: 'warning', completed: 'success',
    failed: 'danger', cancelled: 'info', skipped: 'info',
  }
  return map[status || ''] || 'info'
}
const formatDate = (ts?: number | null) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
const goBack = () => window.history.length > 1 ? router.back() : router.replace(`/pipelines/runs?pipeline=${pipelineId}`)

onMounted(fetchDetail)
</script>

<style scoped>
.page-view { padding: 0; }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-actions { display: flex; gap: 12px; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
.mb-20 { margin-bottom: 20px; }
.error-text { color: #f56c6c; word-break: break-all; }
.log-panel { margin-top: 16px; background: #1e1e1e; border-radius: 6px; max-height: 300px; overflow: auto; }
.log-panel pre {
  margin: 0; padding: 12px; color: #d4d4d4;
  font-family: 'Monaco', 'Menlo', monospace; font-size: 12px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
}
</style>
