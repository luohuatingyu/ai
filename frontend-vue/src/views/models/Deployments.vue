<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">部署管理</h1>
      <el-button @click="refreshData" :loading="loading">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>
    <el-card>
      <div class="filter-bar">
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 150px" @change="onSearch">
          <el-option label="pending" value="pending" />
          <el-option label="running" value="running" />
          <el-option label="stopped" value="stopped" />
          <el-option label="failed" value="failed" />
        </el-select>
        <el-button type="info" plain @click="router.push('/models/registry')">前往模型列表创建部署</el-button>
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
          v-if="deployments.length === 0"
          title="暂无部署记录，可在模型详情页对已注册模型执行「部署服务」"
          type="info" :closable="false" center
        />
        <el-table v-else :data="deployments" border size="small" style="width: 100%">
          <el-table-column prop="id" label="部署 ID" min-width="200" show-overflow-tooltip />
          <el-table-column label="模型" min-width="160" show-overflow-tooltip>
            <template #default="scope">
              <el-link type="primary" @click="viewModel(scope.row.model_id)">
                {{ scope.row.model_name }} v{{ scope.row.model_version }}
              </el-link>
            </template>
          </el-table-column>
          <el-table-column prop="endpoint_name" label="端点名称" width="130">
            <template #default="scope">{{ scope.row.endpoint_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <el-tag size="small" :type="statusTag(scope.row.status) as any" effect="dark">
                {{ scope.row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="副本数" width="80">
            <template #default="scope">{{ scope.row.config?.replicas ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="创建时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="scope">
              <el-button size="small" link type="primary" @click="viewModel(scope.row.model_id)">模型详情</el-button>
              <el-button
                v-if="scope.row.status !== 'stopped'"
                size="small" link type="danger" @click="stopDeployment(scope.row)">停止</el-button>
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
const deployments = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')
const loading = ref(false)
const error = ref('')

// 部署管理页读取全局部署记录接口（旧版误请求 /models 把模型列表当部署展示）
const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/models/deployments', {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      status: statusFilter.value || undefined,
    })
    deployments.value = res.deployments || []
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

const stopDeployment = async (deployment: any) => {
  try {
    await ElMessageBox.confirm(
      `确认停止部署 ${deployment.id}？停止后端点不再对外提供推理。`,
      '停止确认',
      { confirmButtonText: '停止', cancelButtonText: '取消', type: 'warning' }
    )
    await api.delete(`/models/deployments/${deployment.id}`)
    appStore.success('部署已停止')
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('停止失败', err?.message)
  }
}

const statusTag = (status?: string) => {
  const map: Record<string, string> = {
    pending: 'info', running: 'success', stopped: 'warning', failed: 'danger',
  }
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
.filter-bar { display: flex; gap: 12px; margin-bottom: 16px; }
.pagination { padding: 16px 0 4px; display: flex; justify-content: flex-end; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
