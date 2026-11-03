<template>
  <div class="page-view">
    <div class="page-header">
      <div class="header-left">
        <el-button @click="goBack">
          <el-icon><ArrowLeft /></el-icon>
          返回
        </el-button>
        <h1 class="page-title">模型详情</h1>
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
          v-if="detail && detail.status !== 'archived'"
          type="primary" @click="showDeployDialog = true">部署服务</el-button>
        <el-button v-if="detail" type="success" @click="downloadModel">下载产物</el-button>
        <el-button
          v-if="detail && detail.status !== 'archived'"
          type="danger" plain @click="archiveModel">归档</el-button>
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
          <el-descriptions-item label="模型名称">{{ detail.name }}</el-descriptions-item>
          <el-descriptions-item label="版本">{{ detail.version }}</el-descriptions-item>
          <el-descriptions-item label="任务类型">{{ detail.task }}</el-descriptions-item>
          <el-descriptions-item label="框架">{{ detail.framework }}</el-descriptions-item>
          <el-descriptions-item label="来源任务">
            <el-link v-if="detail.source_job_id" type="primary" @click="router.push(`/training/detail/${detail.source_job_id}`)">
              {{ detail.source_job_id }}
            </el-link>
            <span v-else>手动注册</span>
          </el-descriptions-item>
          <el-descriptions-item label="部署端点">{{ detail.deployed_endpoint || '-' }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ formatDate(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDate(detail.updated_at) }}</el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder">
          <el-icon class="is-loading" :size="32"><Loading /></el-icon>
          <p>加载中...</p>
        </div>
      </el-card>

      <el-row :gutter="20" class="mb-20" v-if="detail">
        <el-col :xs="24" :md="12">
          <el-card>
            <template #header><span>评估指标</span></template>
            <div v-if="metricEntries.length > 0" class="metric-grid">
              <div v-for="[key, value] in metricEntries" :key="key" class="metric-item">
                <span class="metric-key">{{ key }}</span>
                <span class="metric-value">{{ Number(value).toFixed(4) }}</span>
              </div>
            </div>
            <el-empty v-else description="暂无指标" :image-size="60" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-card>
            <template #header><span>产物（Artifacts）</span></template>
            <el-descriptions :column="1" border v-if="artifactEntries.length > 0">
              <el-descriptions-item v-for="[key, value] in artifactEntries" :key="key" :label="key">
                <span class="artifact-path">{{ value }}</span>
              </el-descriptions-item>
            </el-descriptions>
            <el-empty v-else description="暂无产物记录" :image-size="60" />
          </el-card>
        </el-col>
      </el-row>

      <el-card v-if="detail">
        <template #header>
          <div class="card-header">
            <span>部署记录</span>
            <el-button
              v-if="detail.status !== 'archived'"
              size="small" type="primary" @click="showDeployDialog = true">新建部署</el-button>
          </div>
        </template>
        <el-alert v-if="deployments.length === 0" title="该模型暂无部署记录" type="info" :closable="false" center />
        <el-table v-else :data="deployments" border size="small">
          <el-table-column prop="id" label="部署 ID" min-width="180" show-overflow-tooltip />
          <el-table-column prop="endpoint_name" label="端点名称" min-width="120">
            <template #default="scope">{{ scope.row.endpoint_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <el-tag size="small" :type="deployStatusTag(scope.row.status) as any">{{ scope.row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 部署对话框 -->
      <el-dialog v-model="showDeployDialog" title="部署推理服务" width="480px">
        <el-form :model="deployForm" label-width="100px">
          <el-form-item label="部署版本">
            <el-input :model-value="detail?.version" disabled />
          </el-form-item>
          <el-form-item label="端点名称">
            <el-input v-model="deployForm.endpoint_name" placeholder="可选，如 sales-api" />
          </el-form-item>
          <el-form-item label="副本数">
            <el-input-number v-model="deployForm.replicas" :min="1" :max="16" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="showDeployDialog = false">取消</el-button>
          <el-button type="primary" :loading="deploying" @click="submitDeploy">创建部署</el-button>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { ArrowLeft, Loading, Refresh, WarningFilled } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const detail = ref<any>(null)
const loading = ref(false)
const error = ref('')
const deploying = ref(false)
const showDeployDialog = ref(false)
const deployForm = reactive({ endpoint_name: '', replicas: 1 })

const modelId = String(route.params.id || '')

const metricEntries = computed(() => Object.entries(detail.value?.metrics || {}))
const artifactEntries = computed(() => Object.entries(detail.value?.artifacts || {}))
const deployments = computed<any[]>(() => detail.value?.deployments || [])

// 后端实际路径为 /api/models/{id}（旧版模板字符串未替换导致 404）
const fetchDetail = async () => {
  loading.value = true
  error.value = ''
  try {
    detail.value = await api.get(`/models/${modelId}`)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const submitDeploy = async () => {
  deploying.value = true
  try {
    const res: any = await api.post(`/models/${modelId}/deploy`, {
      model_version: detail.value.version,
      endpoint_name: deployForm.endpoint_name || null,
      replicas: deployForm.replicas,
      config: {},
    })
    appStore.success('部署已创建', `部署 ID：${res.deployment_id}`)
    showDeployDialog.value = false
    fetchDetail()
  } catch (err: any) {
    appStore.error('部署失败', err?.message)
  } finally {
    deploying.value = false
  }
}

const downloadModel = async () => {
  try {
    const res: any = await api.get(`/models/${modelId}/download`)
    if (res.download_url) {
      window.open(res.download_url, '_blank')
      appStore.success('下载链接已打开', '链接 1 小时内有效')
    } else if (res.local_path) {
      appStore.info('本地存储模式', `产物路径：${res.local_path}（容器内路径，可直接从存储卷获取）`)
    } else {
      appStore.warning('未获取到下载地址')
    }
  } catch (err: any) {
    appStore.error('下载失败', err?.message)
  }
}

// 归档 = 软删除：状态置为 archived，可从列表状态筛选找回
const archiveModel = async () => {
  try {
    await ElMessageBox.confirm(
      `确认归档模型 ${detail.value.name}？归档后默认列表不再展示，可通过状态筛选找回。`,
      '归档确认',
      { confirmButtonText: '归档', cancelButtonText: '取消', type: 'warning' }
    )
    await api.delete(`/models/${modelId}`)
    appStore.success('模型已归档')
    fetchDetail()
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('归档失败', err?.message)
  }
}

const STATUS_TEXT: Record<string, string> = { ready: '可用', archived: '已归档' }
const statusText = (status?: string) => STATUS_TEXT[status || ''] || status || '-'
const statusTag = (status?: string) => {
  const map: Record<string, string> = { ready: 'success', archived: 'info' }
  return map[status || ''] || 'info'
}
const deployStatusTag = (status?: string) => {
  const map: Record<string, string> = {
    pending: 'info', running: 'success', stopped: 'warning', failed: 'danger',
  }
  return map[status || ''] || 'info'
}
const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
const goBack = () => window.history.length > 1 ? router.back() : router.replace('/models/registry')

onMounted(fetchDetail)
</script>

<style scoped>
.page-view { padding: 0; }
.header-left { display: flex; align-items: center; gap: 12px; }
.header-actions { display: flex; gap: 12px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
.mb-20 { margin-bottom: 20px; }
.metric-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.metric-item { background: #f5f7fa; border-radius: 8px; padding: 12px 16px; }
.metric-key { display: block; color: #909399; font-size: 12px; }
.metric-value { font-size: 20px; font-weight: 600; color: #303133; }
.artifact-path { word-break: break-all; font-size: 12px; color: #606266; }
</style>
