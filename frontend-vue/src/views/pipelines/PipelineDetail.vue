<template>
  <div class="page-view">
    <div class="page-header">
      <div class="header-left">
        <el-button @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <h1 class="page-title">Pipeline 详情</h1>
        <el-tag v-if="detail" :type="statusTag(detail.status) as any" effect="dark">
          {{ statusText(detail.status) }}
        </el-tag>
      </div>
      <div class="header-actions">
        <el-button :loading="loading" @click="fetchDetail">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button v-if="detail && detail.status !== 'active'" type="success" plain @click="setStatus('active')">激活</el-button>
        <el-button v-if="detail && detail.status === 'active'" type="warning" plain @click="setStatus('paused')">暂停调度</el-button>
        <el-button v-if="detail && detail.status === 'active'" type="primary" @click="runPipeline">立即运行</el-button>
        <el-button @click="router.push(`/pipelines/runs?pipeline=${pipelineId}`)">执行历史</el-button>
        <el-button @click="router.push(`/pipelines/create?id=${pipelineId}`)">编辑</el-button>
      </div>
    </div>

    <div v-if="error" class="empty-state">
      <el-icon class="empty-state-icon" :size="48"><WarningFilled /></el-icon>
      <p>{{ error }}</p>
      <el-button type="primary" @click="fetchDetail">重试</el-button>
    </div>

    <template v-else>
      <el-card class="mb-20" v-loading="loading">
        <template #header><span>基本信息</span></template>
        <el-descriptions :column="2" border v-if="detail">
          <el-descriptions-item label="名称">{{ detail.name }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusText(detail.status) }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="调度">{{ detail.schedule || '手动触发' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDate(detail.created_at) }}</el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <p>加载中...</p>
        </div>
      </el-card>

      <el-card v-if="detail">
        <template #header>
          <div class="card-header">
            <span>步骤编排（{{ steps.length }} 步）</span>
          </div>
        </template>
        <el-alert v-if="steps.length === 0" title="该 Pipeline 暂无步骤" type="info" :closable="false" center />
        <el-table v-else :data="steps" border size="small">
          <el-table-column type="index" label="#" width="50" />
          <el-table-column prop="name" label="步骤名称" min-width="140" />
          <el-table-column label="类型" width="120">
            <template #default="scope">
              <el-tag size="small">{{ stepTypeText(scope.row.type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="依赖" min-width="140">
            <template #default="scope">
              <el-tag
                v-for="dep in scope.row.depends_on" :key="dep"
                size="small" type="info" effect="plain" class="dep-tag">{{ depName(dep) }}</el-tag>
              <span v-if="!scope.row.depends_on?.length">-</span>
            </template>
          </el-table-column>
          <el-table-column label="配置" min-width="220">
            <template #default="scope">
              <span class="config-text">{{ JSON.stringify(scope.row.config) }}</span>
            </template>
          </el-table-column>
        </el-table>
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

const pipelineId = String(route.params.id || '')
const steps = computed<any[]>(() => detail.value?.steps || [])

// 后端实际路径为 /api/pipelines/{id}（旧版模板字符串未替换导致 404）
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/pipelines/${pipelineId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const setStatus = async (status: string) => {
  try {
    await api.put(`/pipelines/${pipelineId}`, { status })
    appStore.success(status === 'active' ? 'Pipeline 已激活' : 'Pipeline 已暂停调度')
    fetchDetail()
  } catch (err: any) {
    appStore.error('状态更新失败', err?.message)
  }
}

const runPipeline = async () => {
  try {
    const res: any = await api.post(`/pipelines/${pipelineId}/run`, { trigger: 'manual', parameters: {} })
    appStore.success('Pipeline 执行已启动', `执行 ID：${res.run_id}`)
    router.push(`/pipelines/run/${pipelineId}/${res.run_id}`)
  } catch (err: any) {
    appStore.error('运行失败', err?.message)
  }
}

const depName = (depId: string) => {
  const step = steps.value.find((s) => s.id === depId)
  return step?.name || depId
}

const STEP_TEXT: Record<string, string> = {
  dataset_pull: '数据集拉取', dataset_process: '数据预处理', training: '模型训练',
  model_upload: '模型注册', model_deploy: '模型部署', custom_script: '自定义脚本',
  notebook_run: 'Notebook 执行', inference: '批量推理',
}
const stepTypeText = (type?: string) => STEP_TEXT[type || ''] || type || '-'

const STATUS_TEXT: Record<string, string> = {
  draft: '草稿', active: '已激活', paused: '已暂停', archived: '已归档',
}
const statusText = (status?: string) => STATUS_TEXT[status || ''] || status || '-'
const statusTag = (status?: string) => {
  const map: Record<string, string> = { draft: 'info', active: 'success', paused: 'warning', archived: 'info' }
  return map[status || ''] || 'info'
}
const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
const goBack = () => window.history.length > 1 ? router.back() : router.replace('/pipelines/list')

onMounted(fetchDetail)
</script>

<style scoped>
.page-view { padding: 0; }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-actions { display: flex; gap: 12px; flex-wrap: wrap; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
.mb-20 { margin-bottom: 20px; }
.dep-tag { margin-right: 4px; }
.config-text { font-family: 'Monaco', 'Menlo', monospace; font-size: 11px; color: #606266; word-break: break-all; }
</style>
