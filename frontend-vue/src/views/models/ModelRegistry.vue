<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">模型注册表</h1>
      <div class="header-actions">
        <el-button @click="router.push('/models/deployments')">部署管理</el-button>
        <el-button @click="refreshData" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>
    <el-card>
      <div class="filter-bar">
        <el-input
          v-model="searchKeyword" placeholder="搜索模型名称/描述" clearable
          style="width: 220px" @keyup.enter="onSearch" @clear="onSearch"
        />
        <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 140px" @change="onSearch">
          <el-option label="可用（ready）" value="ready" />
          <el-option label="已归档（archived）" value="archived" />
        </el-select>
        <el-select v-model="taskFilter" placeholder="任务类型" clearable style="width: 150px" @change="onSearch">
          <el-option label="分类" value="classification" />
          <el-option label="回归" value="regression" />
          <el-option label="聚类" value="clustering" />
        </el-select>
        <el-button type="primary" @click="onSearch">搜索</el-button>
      </div>

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
        <el-alert
          v-if="models.length === 0"
          :title="statusFilter === 'archived' ? '没有已归档模型' : '暂无注册模型，完成训练后可在任务详情归档注册'"
          type="info" :closable="false" center
        />
        <el-table v-else :data="models" border size="small" style="width: 100%">
          <el-table-column prop="name" label="模型名称" min-width="150" show-overflow-tooltip>
            <template #default="scope">
              <el-link type="primary" @click="viewModel(scope.row.id)">{{ scope.row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="version" label="版本" width="90" />
          <el-table-column label="任务" width="110">
            <template #default="scope">
              <el-tag size="small">{{ taskText(scope.row.task) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="framework" label="框架" width="100" />
          <el-table-column label="指标" min-width="180">
            <template #default="scope">
              <el-tag
                v-for="(value, key) in scope.row.metrics" :key="key"
                size="small" effect="plain" class="metric-tag">
                {{ key }}: {{ Number(value).toFixed(4) }}
              </el-tag>
              <span v-if="!scope.row.metrics || Object.keys(scope.row.metrics).length === 0">-</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <el-tag size="small" :type="statusTag(scope.row.status) as any" effect="dark">
                {{ statusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="注册时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="scope">
              <el-button size="small" link type="primary" @click="viewModel(scope.row.id)">详情</el-button>
              <el-button size="small" link type="success" @click="downloadModel(scope.row)">下载</el-button>
              <el-button
                v-if="scope.row.status !== 'archived'"
                size="small" link type="danger" @click="archiveModel(scope.row)">归档</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination" v-if="total > pageSize">
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
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading, WarningFilled } from '@element-plus/icons-vue'

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
  } finally {
    loading.value = false
  }
}

const onSearch = () => { page.value = 1; refreshData() }
const onPageChange = (p: number) => { page.value = p; refreshData() }
const onPageSizeChange = (size: number) => { pageSize.value = size; page.value = 1; refreshData() }

const viewModel = (id: string) => router.push(`/models/detail/${id}`)

// 归档 = 软删除：状态置为 archived，不删除模型记录与产物
const archiveModel = async (model: any) => {
  try {
    await ElMessageBox.confirm(
      `确认归档模型 ${model.name} v${model.version}？归档后默认列表不再展示，可通过状态筛选找回。`,
      '归档确认',
      { confirmButtonText: '归档', cancelButtonText: '取消', type: 'warning' }
    )
    await api.delete(`/models/${model.id}`)
    appStore.success('模型已归档')
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

const TASK_TEXT: Record<string, string> = {
  classification: '分类', regression: '回归', clustering: '聚类',
}
const taskText = (task?: string) => TASK_TEXT[task || ''] || task || '-'
const STATUS_TEXT: Record<string, string> = { ready: '可用', archived: '已归档' }
const statusText = (status?: string) => STATUS_TEXT[status || ''] || status || '-'
const statusTag = (status?: string) => {
  const map: Record<string, string> = { ready: 'success', archived: 'info' }
  return map[status || ''] || 'info'
}
const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

onMounted(refreshData)
</script>

<style scoped>
.page-view { padding: 0; }
.header-actions { display: flex; gap: 12px; }
.filter-bar { display: flex; gap: 12px; margin-bottom: 16px; }
.metric-tag { margin: 2px 4px 2px 0; }
.pagination { padding: 16px 0 4px; display: flex; justify-content: flex-end; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
