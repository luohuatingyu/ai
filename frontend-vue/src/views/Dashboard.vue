<template>
  <div class="dashboard">
    <div class="page-header">
      <h1 class="page-title">仪表盘</h1>
      <el-button type="primary" @click="refreshData">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>
    
    <!-- 统计卡片 -->
    <el-row :gutter="20" class="mb-20">
      <el-col :xs="24" :sm="12" :md="6" v-for="stat in stats" :key="stat.key">
        <el-card class="stat-card">
          <div class="stat-content">
            <div class="stat-icon" :class="stat.iconClass">
              <el-icon :size="24"><component :is="stat.icon" /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stat.value }}</div>
              <div class="stat-label">{{ stat.label }}</div>
            </div>
          </div>
          <div v-if="stat.trend !== undefined" class="stat-trend" :class="stat.trend >= 0 ? 'positive' : 'negative'">
            <el-icon><component :is="stat.trend >= 0 ? 'ArrowUp' : 'ArrowDown'" /></el-icon>
            <span>{{ Math.abs(stat.trend) }}%</span>
            <span>较上周</span>
          </div>
        </el-card>
      </el-col>
    </el-row>
    
    <!-- 主要内容区 -->
    <el-row :gutter="20">
      <!-- 最近任务 + 资源概览 -->
      <el-col :xs="24" :lg="16">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>最近训练任务</span>
              <el-button size="small" @click="goToTraining">查看全部</el-button>
            </div>
          </template>
          <el-table :data="recentJobs" border size="small" style="width: 100%">
            <el-table-column prop="id" label="任务ID" width="180" />
            <el-table-column prop="task" label="任务类型" width="120">
              <template #default="scope">
                <el-tag :type="getTaskTypeTag(scope.row.task) as any">{{ scope.row.task }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="dataset_name" label="数据集" width="150" />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="scope">
                <el-tag :type="getStatusTag(scope.row.status) as any" effect="dark">
                  {{ getStatusText(scope.row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="160">
              <template #default="scope">
                {{ formatDate(scope.row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="scope">
                <el-button size="small" type="text" @click="viewJob(scope.row.id)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
        
        <!-- 最近模型 -->
        <el-card class="mt-20">
          <template #header>
            <div class="card-header">
              <span>最新注册模型</span>
              <el-button size="small" @click="goToModels">查看全部</el-button>
            </div>
          </template>
          <div class="model-grid" v-if="recentModels.length > 0">
            <div class="model-card-mini" v-for="model in recentModels" :key="model.id" @click="viewModel(model.id)">
              <div class="model-mini-header">
                <span class="model-mini-name">{{ model.name }}</span>
                <el-tag size="small">{{ model.version }}</el-tag>
              </div>
              <div class="model-mini-meta">
                <span>{{ model.task }}</span>
                <span>{{ model.framework }}</span>
              </div>
              <div class="model-mini-metrics">
                <span v-for="(value, key) in model.metrics" :key="key" class="metric-mini">
                  {{ key }}: {{ Number(value).toFixed(4) }}
                </span>
              </div>
            </div>
          </div>
          <div v-else class="empty-state-mini">暂无模型</div>
        </el-card>
      </el-col>
      
      <!-- 右侧：资源状态 + 快捷操作 -->
      <el-col :xs="24" :lg="8">
        <el-card>
          <template #header>
            <span>计算资源概览</span>
          </template>
          <div class="resource-summary">
            <div class="summary-item">
              <div class="summary-label">总 Worker 数</div>
              <div class="summary-value">{{ resourceSummary.total }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">GPU Worker</div>
              <div class="summary-value">{{ resourceSummary.gpu }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">CPU Worker</div>
              <div class="summary-value">{{ resourceSummary.cpu }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">空闲中</div>
              <div class="summary-value">{{ resourceSummary.idle }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">运行中</div>
              <div class="summary-value">{{ resourceSummary.running }}</div>
            </div>
            <div class="summary-item">
              <div class="summary-label">离线</div>
              <div class="summary-value">{{ resourceSummary.offline }}</div>
            </div>
          </div>
        </el-card>
        
        <el-card class="mt-20">
          <template #header>
            <span>快捷操作</span>
          </template>
          <div class="quick-actions">
            <el-button block @click="goToCreateJob">
              <el-icon><Plus /></el-icon>
              创建训练任务
            </el-button>
            <el-button block type="success" @click="goToWorkbench">
              <el-icon><Notebook /></el-icon>
              新建 Notebook
            </el-button>
            <el-button block type="info" @click="goToPipeline">
              <el-icon><Connection /></el-icon>
              创建 Pipeline
            </el-button>
            <el-button block type="warning" @click="goToDatasetImport">
              <el-icon><Upload /></el-icon>
              导入数据集
            </el-button>
          </div>
        </el-card>
        
        <!-- 系统信息 -->
        <el-card class="mt-20">
          <template #header>
            <span>系统信息</span>
          </template>
          <div class="system-info">
            <div class="info-row">
              <span>PyTorch 版本</span>
              <span>{{ systemInfo.torch_version }}</span>
            </div>
            <div class="info-row">
              <span>CUDA 版本</span>
              <span>{{ systemInfo.cuda_version || 'N/A' }}</span>
            </div>
            <div class="info-row">
              <span>GPU 数量</span>
              <span>{{ systemInfo.gpu_count }}</span>
            </div>
            <div class="info-row">
              <span>计算后端</span>
              <span>{{ systemInfo.backend || '-' }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import {
  Refresh, ArrowUp, ArrowDown, Plus, Notebook, Connection, Upload,
  Monitor, Cpu, DataBoard, Box
} from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

const stats = ref([
  { key: 'jobs', label: '训练任务', value: '0', icon: 'Monitor', iconClass: 'blue', trend: 0 },
  { key: 'models', label: '注册模型', value: '0', icon: 'Box', iconClass: 'green', trend: 0 },
  { key: 'datasets', label: '数据集', value: '0', icon: 'Folder', iconClass: 'orange', trend: 0 },
  { key: 'pipelines', label: 'Pipeline', value: '0', icon: 'Connection', iconClass: 'purple', trend: 0 },
])

const recentJobs = ref<any[]>([])
const recentModels = ref<any[]>([])
const resourceSummary = ref({ total: 0, gpu: 0, cpu: 0, idle: 0, running: 0, offline: 0 })
const systemInfo = ref({ torch_version: '-', cuda_version: '-', gpu_count: 0, backend: '-' })

// 各接口独立请求：任一失败只影响对应区块，不拖垮整个仪表盘
const refreshData = async () => {
  appStore.setGlobalLoading(true, '加载仪表盘数据...')
  await Promise.allSettled([
    loadJobStats(),
    loadModelStats(),
    loadDatasetStats(),
    loadPipelineStats(),
    loadWorkers(),
    loadSystem(),
  ])
  appStore.setGlobalLoading(false)
}

// 训练任务统计与最近任务（正确路径为 /api/training/jobs，旧版误请求 /api/jobs）
const loadJobStats = async () => {
  try {
    const jobsRes: any = await api.get('/training/jobs')
    const jobList = Array.isArray(jobsRes) ? jobsRes : (jobsRes.jobs || [])
    stats.value[0].value = jobList.length.toString()
    recentJobs.value = jobList.slice(0, 5).map((job: any) => ({
      ...job,
      dataset_name: job?.spec?.dataset_name || job?.spec?.dataset_id || '-',
      task: job?.spec?.task || job.task,
      created_at: (job.created_at || 0) * 1000,
    }))
  } catch (err: any) {
    appStore.warning('训练任务统计加载失败', err?.message)
  }
}

const loadModelStats = async () => {
  try {
    const modelsRes: any = await api.get('/models', { limit: 8 })
    stats.value[1].value = (modelsRes.total ?? modelsRes.models?.length ?? 0).toString()
    recentModels.value = (modelsRes.models || []).slice(0, 4)
  } catch (err: any) {
    appStore.warning('模型统计加载失败', err?.message)
  }
}

const loadDatasetStats = async () => {
  try {
    const datasetsRes: any = await api.get('/datasets', { limit: 1 })
    stats.value[2].value = (datasetsRes.total ?? datasetsRes.datasets?.length ?? 0).toString()
  } catch (err: any) {
    appStore.warning('数据集统计加载失败', err?.message)
  }
}

const loadPipelineStats = async () => {
  try {
    const pipelinesRes: any = await api.get('/pipelines', { limit: 1 })
    stats.value[3].value = (pipelinesRes.total ?? pipelinesRes.pipelines?.length ?? 0).toString()
  } catch (err: any) {
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
    resourceSummary.value = { total: 0, gpu: 0, cpu: 0, idle: 0, running: 0, offline: 0 }
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
    appStore.warning('系统信息加载失败', err?.message)
  }
}

const getTaskTypeTag = (task: string) => {
  const map: Record<string, string> = {
    classification: 'primary',
    regression: 'success',
    clustering: 'warning',
    time_series_forecasting: 'info',
    llm_pretraining: 'danger',
    llm_sft: 'danger',
    llm_dpo: 'danger',
  }
  return map[task] || 'info'
}

const getStatusTag = (status: string) => {
  const map: Record<string, string> = {
    queued: 'info',
    running: 'primary',
    paused: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info',
  }
  return map[status] || 'info'
}

const getStatusText = (status: string) => {
  const map: Record<string, string> = {
    queued: '排队中',
    running: '运行中',
    paused: '已暂停',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[status] || status
}

const formatDate = (timestamp: number) => {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const goToTraining = () => router.push('/training/jobs')
const goToModels = () => router.push('/models/registry')
const goToPipelines = () => router.push('/pipelines/list')
const goToCreateJob = () => router.push('/training/create')
const goToWorkbench = () => router.push('/workbench/new')
const goToPipeline = () => router.push('/pipelines/create')
const goToDatasetImport = () => router.push('/datasets/import')
const viewJob = (id: string) => router.push(`/training/detail/${id}`)
const viewModel = (id: string) => router.push(`/models/detail/${id}`)

onMounted(() => {
  refreshData()
})
</script>

<style scoped>
.dashboard {
  padding: 0;
}

.stat-card {
  transition: transform 0.2s, box-shadow 0.2s;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 16px;
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.stat-icon.blue { background: #ecf5ff; color: #409eff; }
.stat-icon.green { background: #f0f9eb; color: #67c23a; }
.stat-icon.orange { background: #fdf6ec; color: #e6a23c; }
.stat-icon.purple { background: #f9f0ff; color: #909399; }

.stat-value {
  font-size: 28px;
  font-weight: 600;
  color: #303133;
  line-height: 1;
}

.stat-label {
  font-size: 14px;
  color: #909399;
  margin-top: 4px;
}

.stat-trend {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #ebeef5;
  font-size: 12px;
}

.stat-trend.positive { color: #67c23a; }
.stat-trend.negative { color: #f56c6c; }

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.model-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.model-card-mini {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.model-card-mini:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64, 158, 255, 0.15);
}

.model-mini-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.model-mini-name {
  font-weight: 500;
  font-size: 14px;
  color: #303133;
}

.model-mini-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}

.model-mini-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.metric-mini {
  font-size: 11px;
  padding: 2px 6px;
  background: #f5f7fa;
  border-radius: 3px;
  color: #606266;
}

.resource-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  padding: 8px 0;
}

.summary-item {
  text-align: center;
  padding: 12px;
  background: #fafafa;
  border-radius: 8px;
}

.summary-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}

.summary-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}

.quick-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.system-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
}

.info-row:last-child {
  border-bottom: none;
}

.info-row span:first-child {
  color: #909399;
  font-size: 13px;
}

.info-row span:last-child {
  color: #303133;
  font-weight: 500;
}

.empty-state-mini {
  text-align: center;
  padding: 40px;
  color: #909399;
}

.mb-20 { margin-bottom: 20px; }
.mt-20 { margin-top: 20px; }
</style>