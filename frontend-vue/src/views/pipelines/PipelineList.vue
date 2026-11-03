<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">Pipeline 列表</h1>
      <div class="header-actions">
        <el-button type="primary" @click="router.push('/pipelines/create')">
          <el-icon><Plus /></el-icon>
          创建 Pipeline
        </el-button>
        <el-button @click="refreshData" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>
    <el-card>
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
        <el-alert v-if="pipelines.length === 0" title="暂无 Pipeline，点击右上角创建" type="info" :closable="false" center />
        <el-table v-else :data="pipelines" border size="small" style="width: 100%">
          <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip>
            <template #default="scope">
              <el-link type="primary" @click="viewDetail(scope.row.id)">{{ scope.row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.description || '-' }}</template>
          </el-table-column>
          <el-table-column prop="steps_count" label="步骤数" width="80" />
          <el-table-column label="调度" width="130">
            <template #default="scope">{{ scope.row.schedule || '手动' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="scope">
              <el-tag size="small" :type="statusTag(scope.row.status) as any" effect="dark">
                {{ statusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="scope">
              <el-button
                v-if="scope.row.status === 'active'"
                size="small" link type="success" @click="runPipeline(scope.row)">运行</el-button>
              <el-button size="small" link type="primary" @click="router.push(`/pipelines/runs?pipeline=${scope.row.id}`)">执行历史</el-button>
              <el-button size="small" link type="primary" @click="router.push(`/pipelines/create?id=${scope.row.id}`)">编辑</el-button>
              <el-popconfirm title="确认删除该 Pipeline？执行历史将一并删除。" confirm-button-text="删除" cancel-button-text="取消" @confirm="deletePipeline(scope.row.id)">
                <template #reference>
                  <el-button size="small" link type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
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
import { Refresh, Loading, WarningFilled, Plus } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const pipelines = ref<any[]>([])
const loading = ref(false)
const error = ref('')

const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/pipelines', { limit: 100 })
    pipelines.value = res.pipelines || []
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const viewDetail = (id: string) => router.push(`/pipelines/detail/${id}`)

const runPipeline = async (pipeline: any) => {
  try {
    await ElMessageBox.confirm(
      `确认立即运行 Pipeline「${pipeline.name}」？`,
      '运行确认',
      { confirmButtonText: '运行', cancelButtonText: '取消', type: 'info' }
    )
    const res: any = await api.post(`/pipelines/${pipeline.id}/run`, { trigger: 'manual', parameters: {} })
    appStore.success('Pipeline 执行已启动', `执行 ID：${res.run_id}`)
    router.push(`/pipelines/run/${pipeline.id}/${res.run_id}`)
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('运行失败', err?.message)
  }
}

const deletePipeline = async (id: string) => {
  try {
    await api.delete(`/pipelines/${id}`)
    appStore.success('Pipeline 已删除')
    refreshData()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

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

onMounted(refreshData)
</script>

<style scoped>
.page-view { padding: 0; }
.header-actions { display: flex; gap: 12px; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
