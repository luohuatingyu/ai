<template>
  <PageShell title="任务详情" module="训练任务" :loading="loading && !detail">
    <template #actions>
      <el-button @click="fetchDetail" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button
        v-if="detail && ['running', 'queued'].includes(detail.status)"
        type="warning"
        @click="cancelJob"
      >
        取消任务
      </el-button>
      <el-button v-if="detail && detail.status === 'completed'" type="success" @click="downloadModel">
        <el-icon class="gap-8"><Download /></el-icon>
        下载模型
      </el-button>
    </template>

    <!-- 加载失败 -->
    <SectionCard v-if="error" flush>
      <EmptyState type="error" title="任务加载失败" :description="error" retryable @retry="fetchDetail" />
    </SectionCard>

    <template v-else>
      <!-- 失败原因告警 -->
      <el-alert
        v-if="detail && detail.status === 'failed' && lastError"
        :title="`任务失败：${lastError}`"
        type="error"
        show-icon
        :closable="false"
        class="mb-16"
      />

      <!-- 状态摘要与进度 -->
      <SectionCard v-loading="loading && !detail" title="状态与进度">
        <div v-if="detail" class="status-row">
          <StatusTag :status="detail.status" :map="JOB_STATUS" size="large" />
          <span class="text-secondary text-sm">{{ statusHint }}</span>
        </div>
        <el-progress
          class="mt-12"
          :percentage="progressPercent"
          :status="detail?.status === 'failed' ? 'exception' : (progressPercent >= 100 ? 'success' : undefined)"
        />
        <div v-if="detail" class="progress-meta">
          <span>Epoch {{ progress.epoch }} / {{ progress.epochs_total }}</span>
          <span>任务 ID：<span class="mono">{{ detail.id }}</span></span>
        </div>
        <div v-else class="loading-placeholder" />
      </SectionCard>

      <!-- 基本信息 -->
      <SectionCard v-if="detail" title="基本信息">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="任务类型">{{ taskText(spec.task) }}</el-descriptions-item>
          <el-descriptions-item label="数据集">{{ spec.dataset_name || spec.dataset_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="目标列">{{ spec.target || '-' }}</el-descriptions-item>
          <el-descriptions-item label="设备">{{ detail.device || '自动分配' }}</el-descriptions-item>
          <el-descriptions-item label="计算槽">{{ detail.worker_id || detail.machine_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatFullDateTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ formatFullDateTime(detail.finished_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.last_error || detail.error" label="错误信息" :span="2">
            <span class="error-text">{{ lastError }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </SectionCard>

      <!-- 指标趋势（真实指标数据） -->
      <SectionCard v-if="detail && metrics.length > 0" title="指标趋势" description="按 Epoch 展示训练指标变化" flush>
        <div class="chart-box">
          <VChart class="chart" :option="metricChartOption" autoresize />
        </div>
      </SectionCard>

      <!-- 指标明细 -->
      <SectionCard v-if="detail && metrics.length > 0" title="指标明细" flush>
        <el-table :data="metrics" class="table-card" max-height="320" :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }">
          <el-table-column prop="epoch" label="Epoch" width="90" />
          <el-table-column v-for="key in metricKeys" :key="key" :prop="key" :label="key">
            <template #default="{ row }">{{ formatMetric(row[key]) }}</template>
          </el-table-column>
        </el-table>
      </SectionCard>

      <!-- 训练日志 -->
      <SectionCard v-if="detail" title="训练日志">
        <template #extra>
          <div class="log-toolbar">
            <el-input
              v-model="logFilter"
              placeholder="过滤日志关键词"
              clearable
              size="small"
              style="width: 200px"
              :prefix-icon="Search"
            />
            <el-button size="small" :loading="logsLoading" @click="fetchLogs">
              <el-icon><Refresh /></el-icon>
              刷新日志
            </el-button>
          </div>
        </template>
        <div v-loading="logsLoading" class="log-panel">
          <div v-if="displayLogs.length === 0" class="log-empty">
            {{ logs.length === 0 ? '暂无日志，训练启动后自动产出' : '没有匹配过滤条件的日志' }}
          </div>
          <pre v-else class="log-content">{{ displayLogs.join('\n') }}</pre>
        </div>
      </SectionCard>
    </template>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 任务详情：状态进度、基本信息、指标趋势（真实指标 ECharts）、明细与日志。
 * - 失败任务顶部红色告警展示错误原因；
 * - 日志支持关键词过滤（前端过滤展示）与手动刷新；
 * - 运行中/排队任务每 5 秒自动轮询刷新，页面卸载时清理。
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { JOB_STATUS, TASK_TYPE } from '@/utils/status'
import { formatFullDateTime } from '@/utils/format'
import { Refresh, Download, Search } from '@element-plus/icons-vue'

/** 任务类型中文标签 */
const taskText = (task?: string) => TASK_TYPE[task || '']?.label || task || '-'

// 按需注册 ECharts 模块
use([CanvasRenderer, LineChart, TooltipComponent, LegendComponent, GridComponent])

const route = useRoute()
const appStore = useAppStore()
const detail = ref<any>(null)
const loading = ref(false)
const error = ref('')
const logs = ref<string[]>([])
const logsLoading = ref(false)
const logFilter = ref('')

const jobId = String(route.params.id || '')
const spec = computed(() => detail.value?.spec || {})
const metrics = computed<any[]>(() => detail.value?.metrics || [])
const progress = computed(() => detail.value?.progress || { epoch: 0, epochs_total: 0, percent: 0 })
const lastError = computed(() => detail.value?.last_error || detail.value?.error || '')

const progressPercent = computed(() => Math.min(100, Math.round(progress.value.percent || 0)))

/** 状态提示文案 */
const statusHint = computed(() => {
  const status = detail.value?.status
  if (status === 'running') return '训练进行中，页面每 5 秒自动刷新'
  if (status === 'queued') return '任务排队中，等待分配计算槽'
  if (status === 'completed') return '训练完成，可下载模型产物'
  if (status === 'failed') return '训练失败，请查看错误信息与日志'
  if (status === 'cancelled') return '任务已取消'
  return ''
})

/** 指标键集合（除 epoch 外的全部指标列） */
const metricKeys = computed(() => {
  const keys = new Set<string>()
  for (const m of metrics.value) {
    Object.keys(m || {}).forEach((k) => { if (k !== 'epoch') keys.add(k) })
  }
  return Array.from(keys)
})

/** 过滤后的日志行 */
const displayLogs = computed(() => {
  if (!logFilter.value) return logs.value
  const kw = logFilter.value.toLowerCase()
  return logs.value.filter((line) => line.toLowerCase().includes(kw))
})

/** 指标值展示（保留 4 位小数） */
const formatMetric = (value: any) => {
  if (value === null || value === undefined) return '-'
  const num = Number(value)
  return Number.isNaN(num) ? String(value) : num.toFixed(4)
}

/** 指标趋势折线图配置（每条线对应一个指标键） */
const METRIC_COLORS = ['#3056d3', '#1f9d63', '#d98600', '#d64550', '#7c5cd6', '#0e9db5']
const metricChartOption = computed(() => {
  const keys = metricKeys.value
  if (keys.length === 0) return undefined
  return {
    tooltip: { trigger: 'axis' },
    legend: { top: 0, icon: 'rect', itemWidth: 12, itemHeight: 3, textStyle: { color: '#6b7590', fontSize: 11 } },
    grid: { left: 48, right: 20, top: 36, bottom: 28 },
    xAxis: {
      type: 'category',
      data: metrics.value.map((m) => m.epoch),
      axisLine: { lineStyle: { color: '#d8dde9' } },
      axisLabel: { color: '#6b7590', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisLabel: { color: '#6b7590', fontSize: 11 },
      splitLine: { lineStyle: { color: '#eef1f7' } },
    },
    series: keys.map((key, idx) => ({
      name: key,
      type: 'line',
      smooth: true,
      showSymbol: metrics.value.length <= 30,
      data: metrics.value.map((m) => (m[key] ?? null)),
      lineStyle: { width: 2, color: METRIC_COLORS[idx % METRIC_COLORS.length] },
      itemStyle: { color: METRIC_COLORS[idx % METRIC_COLORS.length] },
    })),
  }
})

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
  logsLoading.value = true
  try {
    const res: any = await api.get(`/training/jobs/${jobId}/logs`, { offset: 0 })
    logs.value = res.lines || []
  } catch (err: any) {
    appStore.error('日志加载失败', err?.message)
  } finally {
    logsLoading.value = false
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

// 运行中/排队任务自动轮询（页面卸载清理）
let pollTimer: ReturnType<typeof setInterval> | null = null
const startPolling = () => {
  const status = detail.value?.status
  if ((status === 'running' || status === 'queued') && !pollTimer) {
    pollTimer = setInterval(fetchDetail, 5000)
  }
}
const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(async () => {
  await fetchDetail()
  fetchLogs()
  startPolling()
})

onUnmounted(stopPolling)
</script>

<style scoped>
.status-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--mtp-ink-500);
  flex-wrap: wrap;
}

.loading-placeholder {
  min-height: 120px;
}

.error-text {
  color: var(--mtp-danger);
  word-break: break-all;
}

.chart-box {
  height: 280px;
  padding: 8px 12px 4px;
}

.chart {
  width: 100%;
  height: 100%;
}

.log-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.log-panel {
  max-height: 360px;
  overflow: auto;
  background: #1e1e2a;
  border-radius: var(--mtp-radius-md);
  min-height: 80px;
}

.log-content {
  margin: 0;
  padding: 12px;
  color: #d4d4d4;
  font-family: var(--mtp-font-mono);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.log-empty {
  text-align: center;
  padding: 32px;
  color: var(--mtp-ink-400);
}
</style>
