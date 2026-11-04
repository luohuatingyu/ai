<template>
  <PageShell :title="detail?.name || '模型详情'" module="模型注册表" :loading="loading && !detail">
    <template #actions>
      <el-button @click="fetchDetail" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button v-if="detail && detail.status !== 'archived'" type="primary" @click="openDeployDialog">
        <el-icon class="gap-8"><Promotion /></el-icon>
        部署服务
      </el-button>
      <el-button v-if="detail" type="success" plain @click="downloadModel">
        <el-icon class="gap-8"><Download /></el-icon>
        下载产物
      </el-button>
      <el-button
        v-if="detail && detail.status !== 'archived'"
        type="danger" plain @click="archiveModel">归档</el-button>
    </template>

    <!-- 加载失败 -->
    <SectionCard v-if="error" flush>
      <EmptyState type="error" title="模型加载失败" :description="error" retryable @retry="fetchDetail" />
    </SectionCard>

    <template v-else>
      <!-- 模型 Hero：状态、版本、关键指标与部署动作一屏呈现 -->
      <div v-if="detail" class="model-hero">
        <div class="model-hero__main">
          <div class="model-hero__row">
            <StatusTag :status="detail.status" :map="MODEL_STATUS" size="large" />
            <span class="model-hero__version">{{ detail.version }}</span>
            <StatusTag :status="detail.task" :map="TASK_TYPE" />
            <span class="model-hero__framework">{{ detail.framework || '-' }}</span>
          </div>
          <div v-if="heroMetrics.length" class="model-hero__metrics">
            <div v-for="m in heroMetrics" :key="m.key" class="hero-metric">
              <span class="hero-metric__key">{{ m.key }}</span>
              <span class="hero-metric__value">{{ m.value }}</span>
            </div>
          </div>
        </div>
        <div class="model-hero__actions">
          <el-button
            v-if="detail.status !== 'archived'"
            type="primary"
            @click="openDeployDialog"
          >
            <el-icon class="gap-8"><Promotion /></el-icon>
            部署服务
          </el-button>
          <el-button type="success" plain @click="downloadModel">
            <el-icon class="gap-8"><Download /></el-icon>
            下载产物
          </el-button>
        </div>
      </div>

      <!-- 基本信息 -->
      <SectionCard v-loading="loading && !detail" title="基本信息">
        <template #extra>
          <StatusTag v-if="detail" :status="detail.status" :map="MODEL_STATUS" size="large" />
        </template>
        <el-descriptions v-if="detail" :column="2" border>
          <el-descriptions-item label="版本">{{ detail.version }}</el-descriptions-item>
          <el-descriptions-item label="任务类型">
            <StatusTag :status="detail.task" :map="TASK_TYPE" />
          </el-descriptions-item>
          <el-descriptions-item label="框架">{{ detail.framework || '-' }}</el-descriptions-item>
          <el-descriptions-item label="部署端点">{{ detail.deployed_endpoint || '-' }}</el-descriptions-item>
          <el-descriptions-item label="来源任务">
            <el-link v-if="detail.source_job_id" type="primary" underline="never" class="mono text-sm" @click="router.push(`/training/detail/${detail.source_job_id}`)">
              {{ detail.source_job_id }}
            </el-link>
            <span v-else class="text-secondary">手动注册</span>
          </el-descriptions-item>
          <el-descriptions-item label="注册时间">{{ formatFullDateTime(detail.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ detail.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatFullDateTime(detail.updated_at) }}</el-descriptions-item>
        </el-descriptions>
        <div v-else class="loading-placeholder" />
      </SectionCard>

      <!-- 指标与产物 -->
      <div class="detail-grid" v-if="detail">
        <SectionCard title="评估指标" description="训练完成时的评估结果">
          <div v-if="metricEntries.length > 0" class="metric-grid">
            <div v-for="[key, value] in metricEntries" :key="key" class="metric-item">
              <span class="metric-key">{{ key }}</span>
              <span class="metric-value">{{ Number(value).toFixed(4) }}</span>
            </div>
          </div>
          <EmptyState v-else title="暂无指标" description="该模型未记录评估指标" />
        </SectionCard>

        <SectionCard title="产物（Artifacts）" description="模型文件与配套文件路径">
          <el-descriptions v-if="artifactEntries.length > 0" :column="1" border>
            <el-descriptions-item v-for="[key, value] in artifactEntries" :key="key" :label="key">
              <span class="artifact-path mono">{{ value }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <EmptyState v-else title="暂无产物记录" />
        </SectionCard>
      </div>

      <!-- 部署记录 -->
      <SectionCard v-if="detail" title="部署记录" flush>
        <template #extra>
          <el-button v-if="detail.status !== 'archived'" size="small" type="primary" plain @click="openDeployDialog">
            新建部署
          </el-button>
        </template>
        <el-table v-if="deployments.length > 0" :data="deployments" class="table-card" :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }">
          <el-table-column prop="id" label="部署 ID" min-width="190" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono text-sm">{{ row.id }}</span></template>
          </el-table-column>
          <el-table-column prop="endpoint_name" label="端点名称" min-width="120">
            <template #default="{ row }">{{ row.endpoint_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <StatusTag :status="row.status" :map="DEPLOY_STATUS" />
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="130">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
        <EmptyState v-else title="该模型暂无部署记录" description="创建部署后可对外提供推理服务" />
      </SectionCard>
    </template>

    <!-- 部署对话框 -->
    <el-dialog v-model="showDeployDialog" title="部署推理服务" width="480px" destroy-on-close>
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
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 模型详情：概览（版本/任务/来源任务）、评估指标、产物路径与部署记录。
 * - 非归档模型可发起部署（对话框提交）与归档（软删除，带确认）；
 * - 下载产物走后端签发链接，本地存储模式提示容器内路径。
 */
import { ref, computed, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { MODEL_STATUS, TASK_TYPE, DEPLOY_STATUS } from '@/utils/status'
import { formatDateTime, formatFullDateTime } from '@/utils/format'
import { Refresh, Download, Promotion } from '@element-plus/icons-vue'

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

/** 打开部署对话框时重置表单 */
const openDeployDialog = () => {
  deployForm.endpoint_name = ''
  deployForm.replicas = 1
  showDeployDialog.value = true
}

/** Hero 关键指标：取前 4 项指标强调展示（最新训练结果） */
const heroMetrics = computed(() =>
  metricEntries.value
    .slice(0, 4)
    .map(([key, value]) => ({ key, value: Number(value).toFixed(4) }))
)

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
      `确认归档模型「${detail.value.name}」？归档后默认列表不再展示，可通过状态筛选找回。`,
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

onMounted(fetchDetail)
</script>

<style scoped>
/* 模型 Hero：状态 + 版本 + 关键指标 + 部署动作 */
.model-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  background: var(--mtp-bg-card);
  border: 1px solid var(--mtp-ink-100);
  border-left: 4px solid var(--mtp-module-accent, var(--mtp-brand-600));
  border-radius: var(--mtp-radius-lg);
  box-shadow: var(--mtp-shadow-card);
  padding: 18px 20px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.model-hero__main {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.model-hero__row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.model-hero__version {
  font-size: 18px;
  font-weight: 700;
  color: var(--mtp-ink-900);
  font-family: var(--mtp-font-mono);
}

.model-hero__framework {
  font-size: 12.5px;
  color: var(--mtp-ink-500);
}

.model-hero__metrics {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.model-hero__actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  flex-shrink: 0;
}

.hero-metric {
  min-width: 86px;
  padding: 8px 14px;
  background: var(--mtp-bg-subtle);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-md);
  text-align: center;
}

.hero-metric__key {
  display: block;
  font-size: 11.5px;
  color: var(--mtp-ink-400);
  margin-bottom: 2px;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-metric__value {
  font-size: 17px;
  font-weight: 700;
  color: var(--mtp-ink-900);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .model-hero__actions {
    width: 100%;
  }
}

.loading-placeholder {
  min-height: 160px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
  margin-bottom: 16px;
}

@media (max-width: 992px) {
  .detail-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.metric-item {
  background: var(--mtp-bg-subtle);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-md);
  padding: 12px 16px;
}

.metric-key {
  display: block;
  color: var(--mtp-ink-400);
  font-size: 12px;
  margin-bottom: 2px;
}

.metric-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--mtp-ink-900);
  font-variant-numeric: tabular-nums;
}

.artifact-path {
  word-break: break-all;
  font-size: 12px;
  color: var(--mtp-ink-700);
}
</style>
