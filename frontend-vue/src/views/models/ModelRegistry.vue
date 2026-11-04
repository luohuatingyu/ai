<template>
  <PageShell title="模型注册表" module="模型注册表" description="平台模型资产清单：指标、版本与来源任务">
    <template #actions>
      <el-button @click="router.push('/models/deployments')">
        <el-icon class="gap-8"><Promotion /></el-icon>
        部署管理
      </el-button>
      <el-button @click="refreshData" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
    </template>

    <!-- 筛选：默认只展示可用模型，归档可切换找回 -->
    <FilterBar>
      <el-input
        v-model="searchKeyword"
        placeholder="搜索模型名称 / 描述"
        clearable
        style="width: 220px"
        :prefix-icon="Search"
        @keyup.enter="onSearch"
        @clear="onSearch"
      />
      <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 150px" @change="onSearch">
        <el-option label="可用（ready）" value="ready" />
        <el-option label="已归档（archived）" value="archived" />
      </el-select>
      <el-select v-model="taskFilter" placeholder="任务类型" clearable style="width: 150px" @change="onSearch">
        <el-option label="分类" value="classification" />
        <el-option label="回归" value="regression" />
        <el-option label="聚类" value="clustering" />
      </el-select>
      <el-button type="primary" plain @click="onSearch">搜索</el-button>
      <template #actions>
        <span class="text-xs text-secondary">共 {{ formatInteger(total) }} 个模型</span>
      </template>
    </FilterBar>

    <!-- 模型表格 -->
    <SectionCard flush>
      <el-table
        v-if="models.length > 0"
        v-loading="loading"
        :data="models"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="name" label="模型名称" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="model-name">
              <el-link type="primary" underline="never" @click="viewModel(row.id)">{{ row.name }}</el-link>
              <span class="text-xs text-ink-400">v{{ row.version }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="任务" width="100">
          <template #default="{ row }">
            <StatusTag :status="row.task" :map="TASK_TYPE" />
          </template>
        </el-table-column>
        <el-table-column prop="framework" label="框架" width="110">
          <template #default="{ row }">{{ row.framework || '-' }}</template>
        </el-table-column>
        <el-table-column label="评估指标" min-width="200">
          <template #default="{ row }">
            <template v-if="row.metrics && Object.keys(row.metrics).length > 0">
              <span v-for="(value, key) in row.metrics" :key="key" class="metric-tag">
                {{ key }}: {{ Number(value).toFixed(4) }}
              </span>
            </template>
            <span v-else class="text-ink-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <StatusTag :status="row.status" :map="MODEL_STATUS" />
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="120">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" link type="primary" @click="viewModel(row.id)">详情</el-button>
              <el-button size="small" link type="success" @click="downloadModel(row)">下载</el-button>
              <el-button
                v-if="row.status !== 'archived'"
                size="small" link type="danger" @click="archiveModel(row)">归档</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else-if="error"
        type="error"
        title="模型加载失败"
        :description="error"
        retryable
        @retry="refreshData"
      />
      <EmptyState
        v-else-if="hasFilter"
        title="没有匹配的模型"
        description="调整搜索关键词、状态或任务类型后重试"
      >
        <template #actions>
          <el-button size="small" @click="resetFilters">清除筛选</el-button>
        </template>
      </EmptyState>
      <EmptyState
        v-else
        :title="statusFilter === 'archived' ? '没有已归档模型' : '暂无注册模型'"
        :description="statusFilter === 'archived' ? '归档采用软删除，完成训练后可在任务详情归档注册' : '完成训练后可在任务详情页将模型注册到平台'"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="router.push('/training/jobs')">查看训练任务</el-button>
        </template>
      </EmptyState>

      <div class="table-pagination" v-if="total > pageSize">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="onPageSizeChange"
          @current-change="onPageChange"
        />
      </div>
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 模型注册表：搜索/状态/任务三维筛选 + 模型资产表格。
 * - 默认只展示可用（ready）模型；归档是软删除，可切换状态筛选找回；
 * - 归档带确认弹窗（展示名称与版本）；下载走后端签发链接或本地路径提示。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import FilterBar from '@/components/common/FilterBar.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { MODEL_STATUS, TASK_TYPE } from '@/utils/status'
import { formatDateTime, formatInteger } from '@/utils/format'
import { Refresh, Search, Promotion } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const models = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const searchKeyword = ref('')
const statusFilter = ref('ready')
const taskFilter = ref('')
const loading = ref(false)
const error = ref('')

/** 是否处于筛选状态（默认 ready 筛选不算） */
const hasFilter = computed(() => Boolean(searchKeyword.value) || Boolean(taskFilter.value) || (Boolean(statusFilter.value) && statusFilter.value !== 'ready'))

// 默认只展示可用模型；归档采用软删除，可切换状态筛选查看
const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/models', {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      search: searchKeyword.value || undefined,
      status: statusFilter.value || undefined,
      task: taskFilter.value || undefined,
    })
    models.value = res.models || []
    total.value = res.total || 0
  } catch (err: any) {
    error.value = err?.message || '加载失败'
    appStore.error('模型加载失败', err?.message)
  } finally {
    loading.value = false
  }
}

const onSearch = () => {
  page.value = 1
  refreshData()
}

const resetFilters = () => {
  searchKeyword.value = ''
  taskFilter.value = ''
  statusFilter.value = 'ready'
  page.value = 1
  refreshData()
}

const onPageChange = (p: number) => {
  page.value = p
  refreshData()
}

const onPageSizeChange = (size: number) => {
  pageSize.value = size
  page.value = 1
  refreshData()
}

const viewModel = (id: string) => router.push(`/models/detail/${id}`)

// 归档 = 软删除：状态置为 archived，不删除模型记录与产物
const archiveModel = async (model: any) => {
  try {
    await ElMessageBox.confirm(
      `确认归档模型「${model.name}」v${model.version}？归档后默认列表不再展示，可通过状态筛选找回。`,
      '归档确认',
      { confirmButtonText: '归档', cancelButtonText: '取消', type: 'warning' }
    )
    await api.delete(`/models/${model.id}`)
    appStore.success('模型已归档', model.name)
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('归档失败', err?.message)
  }
}

const downloadModel = async (model: any) => {
  try {
    const res: any = await api.get(`/models/${model.id}/download`)
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

onMounted(refreshData)
</script>

<style scoped>
.model-name {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.metric-tag {
  display: inline-block;
  font-size: 11px;
  padding: 2px 8px;
  background: var(--mtp-bg-subtle);
  border-radius: 4px;
  color: var(--mtp-ink-700);
  font-family: var(--mtp-font-mono);
  margin: 2px 6px 2px 0;
}
</style>
