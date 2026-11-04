<template>
  <PageShell title="仪表盘" module="总览" description="平台运行总览：训练进展、模型资产、计算资源与常用入口">
    <template #actions>
      <el-button @click="refreshData" :loading="refreshing">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button type="primary" @click="router.push('/training/create')">
        <el-icon class="gap-8"><Plus /></el-icon>
        创建训练任务
      </el-button>
    </template>

    <!-- 需要关注：失败 / 排队任务置顶直达，点击跳转到筛选后的任务列表 -->
    <div
      v-if="attentionFailed > 0 || attentionQueued > 0"
      class="attention-strip"
      :class="{ 'is-alert': attentionFailed > 0 }"
      role="status"
    >
      <el-icon class="attention-strip__icon"><WarningFilled v-if="attentionFailed > 0" /><InfoFilled v-else /></el-icon>
      <span class="attention-strip__label">{{ attentionFailed > 0 ? '存在异常任务，需要处理' : '有任务正在等待' }}</span>
      <button v-if="attentionFailed > 0" type="button" class="attention-chip is-danger" @click="goJobs('failed')">
        失败 {{ attentionFailed }}
      </button>
      <button v-if="attentionQueued > 0" type="button" class="attention-chip is-warning" @click="goJobs('queued')">
        排队中 {{ attentionQueued }}
      </button>
      <button v-if="attentionRunning > 0" type="button" class="attention-chip is-info" @click="goJobs('running')">
        运行中 {{ attentionRunning }}
      </button>
    </div>

    <!-- 关键指标（点击进入对应模块） -->
    <div class="stat-grid">
      <StatCard
        v-for="stat in statCards"
        :key="stat.key"
        :label="stat.label"
        :value="stat.value"
        :icon="stat.icon"
        :tone="stat.tone"
        :hint="stat.hint"
        clickable
        @click="router.push(stat.to)"
      />
    </div>

    <div class="dash-grid">
      <!-- 左列：最近任务 / 最新模型 -->
      <div class="dash-col dash-col--main">
        <SectionCard title="最近训练任务" description="最新创建的训练任务" flush>
          <template #extra>
            <el-button link type="primary" @click="router.push('/training/jobs')">查看全部</el-button>
          </template>
          <el-table
            v-if="recentJobs.length"
            :data="recentJobs"
            class="table-card"
            :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
          >
            <el-table-column prop="name" label="任务名称" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link type="primary" underline="never" @click="viewJob(row.id)">
                  {{ row.name || row.id }}
                </el-link>
              </template>
            </el-table-column>
            <el-table-column prop="task" label="任务类型" width="110">
              <template #default="{ row }">
                <StatusTag :status="row.task" :map="TASK_TYPE" />
              </template>
            </el-table-column>
            <el-table-column prop="dataset_name" label="数据集" min-width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ row.dataset_name }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="96">
              <template #default="{ row }">
                <StatusTag :status="row.status" :map="JOB_STATUS" />
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="120">
              <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <EmptyState
            v-else-if="sectionErrors.jobs"
            type="error"
            title="训练任务加载失败"
            :description="sectionErrors.jobs"
            retryable
            @retry="refreshData"
          />
          <EmptyState v-else title="暂无训练任务" description="创建第一个训练任务，开始模型训练">
            <template #actions>
              <el-button type="primary" size="small" @click="router.push('/training/create')">创建训练任务</el-button>
            </template>
          </EmptyState>
        </SectionCard>

        <SectionCard title="最新注册模型" description="最近注册的模型资产">
          <template #extra>
            <el-button link type="primary" @click="router.push('/models/registry')">查看全部</el-button>
          </template>
          <div v-if="recentModels.length" class="model-grid">
            <button
              v-for="model in recentModels"
              :key="model.id"
              type="button"
              class="model-mini"
              @click="viewModel(model.id)"
            >
              <div class="model-mini__head">
                <span class="model-mini__name">{{ model.name }}</span>
                <el-tag size="small" effect="plain">{{ model.version }}</el-tag>
              </div>
              <div class="model-mini__meta">
                <span>{{ TASK_TYPE[model.task]?.label || model.task }}</span>
                <span>{{ model.framework }}</span>
              </div>
              <div class="model-mini__metrics">
                <span v-for="(value, key) in model.metrics" :key="key" class="metric-mini">
                  {{ key }}: {{ Number(value).toFixed(4) }}
                </span>
              </div>
            </button>
          </div>
          <EmptyState v-else-if="sectionErrors.models" type="error" title="模型加载失败" :description="sectionErrors.models" retryable @retry="refreshData" />
          <EmptyState v-else title="暂无注册模型" description="完成训练后可在模型注册表登记模型" />
        </SectionCard>
      </div>

      <!-- 右列：状态分布 / 资源 / 系统信息 / 快捷操作 -->
      <div class="dash-col dash-col--side">
        <SectionCard title="任务状态分布" description="基于全部训练任务统计">
          <div v-if="hasJobDist" class="chart-box">
            <VChart class="chart" :option="jobStatusOption" autoresize />
          </div>
          <EmptyState v-else title="暂无任务数据" description="任务创建后将在此展示状态分布" />
        </SectionCard>

        <SectionCard title="计算资源概览" description="训练节点与负载">
          <template #extra>
            <el-button link type="primary" @click="router.push('/clusters/workers')">详情</el-button>
          </template>
          <div class="resource-tiles">
            <div class="resource-tile">
              <div class="resource-tile__value">{{ resourceSummary.total }}</div>
              <div class="resource-tile__label">总 Worker</div>
            </div>
            <div class="resource-tile">
              <div class="resource-tile__value">{{ resourceSummary.gpu }}</div>
              <div class="resource-tile__label">GPU</div>
            </div>
            <div class="resource-tile">
              <div class="resource-tile__value">{{ resourceSummary.cpu }}</div>
              <div class="resource-tile__label">CPU</div>
            </div>
            <div class="resource-tile">
              <div class="resource-tile__value text-success">{{ resourceSummary.idle }}</div>
              <div class="resource-tile__label">空闲中</div>
            </div>
            <div class="resource-tile">
              <div class="resource-tile__value text-running">{{ resourceSummary.running }}</div>
              <div class="resource-tile__label">运行中</div>
            </div>
            <div class="resource-tile">
              <div class="resource-tile__value text-secondary">0</div>
              <div class="resource-tile__label">离线</div>
            </div>
          </div>
          <el-progress
            class="mt-12"
            :percentage="runningPercent"
            :stroke-width="8"
            :color="RUNNING_COLOR"
            :format="() => `负载 ${runningPercent}%`"
          />
          <EmptyState v-if="sectionErrors.workers" type="error" title="资源数据加载失败" :description="sectionErrors.workers" retryable @retry="refreshData" />
        </SectionCard>

        <SectionCard title="系统信息">
          <div class="system-info">
            <div class="info-row"><span>PyTorch 版本</span><span>{{ systemInfo.torch_version }}</span></div>
            <div class="info-row"><span>CUDA 版本</span><span>{{ systemInfo.cuda_version }}</span></div>
            <div class="info-row"><span>GPU 数量</span><span>{{ systemInfo.gpu_count }}</span></div>
            <div class="info-row"><span>计算后端</span><span>{{ systemInfo.backend }}</span></div>
          </div>
        </SectionCard>

        <SectionCard title="快捷操作">
          <div class="quick-actions">
            <el-button @click="router.push('/workbench/new')">
              <el-icon class="gap-8"><Notebook /></el-icon>
              新建 Notebook
            </el-button>
            <el-button @click="router.push('/pipelines/create')">
              <el-icon class="gap-8"><Connection /></el-icon>
              创建 Pipeline
            </el-button>
            <el-button @click="router.push('/datasets/import')">
              <el-icon class="gap-8"><Upload /></el-icon>
              导入数据集
            </el-button>
            <el-button @click="router.push('/templates/library')">
              <el-icon class="gap-8"><Document /></el-icon>
              浏览模板库
            </el-button>
          </div>
        </SectionCard>
      </div>
    </div>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 仪表盘：关键指标、最近任务/模型、任务状态分布（真实数据）、
 * 计算资源与系统信息。
 * 数据策略：各接口独立请求（Promise.allSettled），任一失败只影响对应
 * 区块并在区块内提供重试，不拖垮整个仪表盘。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import StatCard from '@/components/common/StatCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { JOB_STATUS, TASK_TYPE } from '@/utils/status'
import { formatDateTime } from '@/utils/format'
import {
  Refresh, Plus, Notebook, Connection, Upload, Document,
  Monitor, Folder, Box, WarningFilled, InfoFilled,
} from '@element-plus/icons-vue'

// 按需注册 ECharts 模块（树摇打包）
use([CanvasRenderer, PieChart, TooltipComponent, LegendComponent])

const router = useRouter()
const appStore = useAppStore()

/** 关键指标卡片（value 由各加载函数回填） */
type StatTone = 'blue' | 'green' | 'orange' | 'red' | 'purple' | 'cyan' | 'gray'
const statCards = ref([
  { key: 'jobs', label: '训练任务', value: '0', icon: Monitor, tone: 'blue', to: '/training/jobs', hint: '全部任务' },
  { key: 'models', label: '注册模型', value: '0', icon: Box, tone: 'green', to: '/models/registry', hint: '模型资产' },
  { key: 'datasets', label: '数据集', value: '0', icon: Folder, tone: 'orange', to: '/datasets/list', hint: '数据资产' },
  { key: 'pipelines', label: 'Pipeline', value: '0', icon: Connection, tone: 'purple', to: '/pipelines/list', hint: '编排流程' },
] as Array<{ key: string; label: string; value: string; icon: any; tone: StatTone; to: string; hint: string }>)

const recentJobs = ref<any[]>([])
const recentModels = ref<any[]>([])
const resourceSummary = ref({ total: 0, gpu: 0, cpu: 0, idle: 0, running: 0, offline: 0 })
const systemInfo = ref({ torch_version: '-', cuda_version: '-', gpu_count: 0, backend: '-' })
const statusCounts = ref<Record<string, number>>({})

/** 各区块加载错误（展示在对应区块内，支持重试） */
const sectionErrors = ref<Record<string, string>>({})

const refreshing = ref(false)

// 任务状态分布（真实数据：由任务列表聚合）
const RUNNING_COLOR = '#3056d3'
const STATUS_COLORS: Record<string, string> = {
  queued: '#97a0b5',
  running: '#3056d3',
  paused: '#d98600',
  completed: '#1f9d63',
  failed: '#d64550',
  cancelled: '#b8c0d4',
}

/** 是否有可绘制的状态分布数据 */
const hasJobDist = computed(() => Object.values(statusCounts.value).some((v) => v > 0))

/** 运行负载百分比（空闲判定与运行中节点数换算，无 worker 时为 0） */
const runningPercent = computed(() => {
  const { total, running } = resourceSummary.value
  if (!total) return 0
  return Math.min(100, Math.round((running / total) * 100))
})

/** 状态分布环形图配置（全部状态都有数据才绘制） */
const jobStatusOption = computed<any>(() => {
  const entries = Object.entries(statusCounts.value).filter(([, count]) => count > 0)
  if (entries.length === 0) return undefined
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: {
      bottom: 0,
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: '#6b7590', fontSize: 11 },
    },
    series: [
      {
        type: 'pie',
        radius: ['50%', '74%'],
        center: ['50%', '40%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
        label: { show: false },
        data: entries.map(([status, count]) => ({
          name: JOB_STATUS[status]?.label || status,
          value: count,
          itemStyle: { color: STATUS_COLORS[status] || '#b8c0d4' },
        })),
      },
    ],
  }
})

/** 统一刷新：并行加载全部区块，逐块容错 */
const refreshData = async () => {
  refreshing.value = true
  sectionErrors.value = {}
  await Promise.allSettled([
    loadJobStats(),
    loadModelStats(),
    loadDatasetStats(),
    loadPipelineStats(),
    loadWorkers(),
    loadSystem(),
  ])
  refreshing.value = false
}

/** 训练任务统计、最近任务与状态分布（正确路径为 /api/training/jobs） */
const loadJobStats = async () => {
  try {
    const jobsRes: any = await api.get('/training/jobs')
    const jobList = Array.isArray(jobsRes) ? jobsRes : (jobsRes.jobs || [])
    statCards.value[0].value = jobList.length.toString()
    recentJobs.value = jobList.slice(0, 5).map((job: any) => ({
      ...job,
      dataset_name: job?.spec?.dataset_name || job?.spec?.dataset_id || '-',
      task: job?.spec?.task || job.task,
      created_at: (job.created_at || 0) * 1000,
    }))
    // 聚合全部任务的状态分布
    const counts: Record<string, number> = {}
    for (const job of jobList) {
      const status = job.status || 'unknown'
      counts[status] = (counts[status] || 0) + 1
    }
    statusCounts.value = counts
  } catch (err: any) {
    sectionErrors.value.jobs = err?.message || '网络错误'
    appStore.warning('训练任务统计加载失败', err?.message)
  }
}

const loadModelStats = async () => {
  try {
    const modelsRes: any = await api.get('/models', { limit: 8 })
    statCards.value[1].value = (modelsRes.total ?? modelsRes.models?.length ?? 0).toString()
    recentModels.value = (modelsRes.models || []).slice(0, 4)
  } catch (err: any) {
    sectionErrors.value.models = err?.message || '网络错误'
    appStore.warning('模型统计加载失败', err?.message)
  }
}

const loadDatasetStats = async () => {
  try {
    const datasetsRes: any = await api.get('/datasets', { limit: 1 })
    statCards.value[2].value = (datasetsRes.total ?? datasetsRes.datasets?.length ?? 0).toString()
  } catch (err: any) {
    sectionErrors.value.datasets = err?.message || '网络错误'
    appStore.warning('数据集统计加载失败', err?.message)
  }
}

const loadPipelineStats = async () => {
  try {
    const pipelinesRes: any = await api.get('/pipelines', { limit: 1 })
    statCards.value[3].value = (pipelinesRes.total ?? pipelinesRes.pipelines?.length ?? 0).toString()
  } catch (err: any) {
    sectionErrors.value.pipelines = err?.message || '网络错误'
    appStore.warning('Pipeline 统计加载失败', err?.message)
  }
}

const loadWorkers = async () => {
  try {
    const workersRes: any = await api.get('/clusters/workers/summary')
    const details = workersRes.details || []
    resourceSummary.value = {
      total: workersRes.total ?? details.length,
      gpu: workersRes.gpu ?? details.filter((w: any) => w.device !== 'cpu').length,
      cpu: workersRes.cpu ?? details.filter((w: any) => w.device === 'cpu').length,
      idle: details.filter((w: any) => (w.state || 'idle') === 'idle' && !w.current_job).length,
      running: details.filter((w: any) => w.current_job).length,
      offline: 0,
    }
  } catch (err: any) {
    sectionErrors.value.workers = err?.message || '网络错误'
    appStore.warning('计算资源加载失败', err?.message)
  }
}

const loadSystem = async () => {
  try {
    const systemRes: any = await api.get('/system')
    systemInfo.value = {
      torch_version: systemRes.torch_version || '-',
      cuda_version: systemRes.cuda_version || '-',
      gpu_count: systemRes.gpu_count ?? 0,
      backend: systemRes.backend || '-',
    }
  } catch (err: any) {
    sectionErrors.value.system = err?.message || '网络错误'
    appStore.warning('系统信息加载失败', err?.message)
  }
}

const viewJob = (id: string) => router.push(`/training/detail/${id}`)
const viewModel = (id: string) => router.push(`/models/detail/${id}`)

/** 需要关注的任务数量（失败 / 排队 / 运行，来自任务状态聚合） */
const attentionFailed = computed(() => statusCounts.value.failed || 0)
const attentionQueued = computed(() => statusCounts.value.queued || 0)
const attentionRunning = computed(() => statusCounts.value.running || 0)

/** 点击关注条跳转到按状态筛选后的任务列表 */
const goJobs = (status: string) => router.push(`/training/jobs?status=${status}`)

onMounted(() => {
  refreshData()
})
</script>

<style scoped>
/* 需要关注条：有失败任务时红色告警底，仅排队时品牌色提示底 */
.attention-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 10px 16px;
  margin-bottom: 16px;
  border-radius: var(--mtp-radius-lg);
  border: 1px solid var(--mtp-brand-100);
  background: var(--mtp-brand-50);
  color: var(--mtp-brand-700);
  font-size: 13px;
}

.attention-strip.is-alert {
  border-color: #f5c6cb;
  background: #fdf1f2;
  color: var(--mtp-danger);
}

.attention-strip__icon {
  flex-shrink: 0;
}

.attention-strip__label {
  font-weight: 600;
}

.attention-chip {
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 2px 12px;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}

.attention-chip:hover {
  transform: translateY(-1px);
  box-shadow: var(--mtp-shadow-card);
}

.attention-chip.is-danger  { background: var(--mtp-danger); color: #fff; }
.attention-chip.is-warning { background: var(--mtp-warning); color: #fff; }
.attention-chip.is-info    { background: var(--mtp-brand-600); color: #fff; }

/* 双列布局：主列自适应 + 侧列固定，窄屏单列 */
.dash-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1200px) {
  .dash-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.dash-col {
  min-width: 0;
}

/* 模型小卡 */
.model-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 768px) {
  .model-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.model-mini {
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-md);
  padding: 12px;
  cursor: pointer;
  background: var(--mtp-bg-card);
  text-align: left;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.model-mini:hover {
  border-color: var(--mtp-brand-500);
  box-shadow: var(--mtp-shadow-card);
}

.model-mini__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}

.model-mini__name {
  font-weight: 500;
  font-size: 14px;
  color: var(--mtp-ink-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-mini__meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--mtp-ink-400);
  margin-bottom: 8px;
}

.model-mini__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.metric-mini {
  font-size: 11px;
  padding: 2px 6px;
  background: var(--mtp-bg-subtle);
  border-radius: 4px;
  color: var(--mtp-ink-700);
  font-family: var(--mtp-font-mono);
}

/* 图表容器 */
.chart-box {
  height: 240px;
}

.chart {
  width: 100%;
  height: 100%;
}

/* 资源瓦片 */
.resource-tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.resource-tile {
  text-align: center;
  padding: 10px 4px;
  background: var(--mtp-bg-subtle);
  border-radius: var(--mtp-radius-md);
}

.resource-tile__value {
  font-size: 20px;
  font-weight: 600;
  color: var(--mtp-ink-900);
  font-variant-numeric: tabular-nums;
}

.resource-tile__label {
  font-size: 12px;
  color: var(--mtp-ink-400);
  margin-top: 2px;
}

.text-success { color: var(--mtp-success); }
.text-running { color: var(--mtp-brand-600); }

/* 系统信息 */
.system-info {
  display: flex;
  flex-direction: column;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--mtp-ink-100);
  font-size: 13px;
}

.info-row:last-child {
  border-bottom: none;
}

.info-row span:first-child {
  color: var(--mtp-ink-500);
}

.info-row span:last-child {
  color: var(--mtp-ink-900);
  font-weight: 500;
  font-family: var(--mtp-font-mono);
}

/* 快捷操作 */
.quick-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.quick-actions .el-button {
  margin-left: 0;
  width: 100%;
}
</style>
