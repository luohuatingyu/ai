<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">执行历史</h1>
      <el-button @click="refreshData" :loading="loading">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>
    <el-card>
      <div class="filter-bar">
        <el-select
          v-model="selectedPipeline" placeholder="选择 Pipeline" style="width: 280px"
          :loading="pipelinesLoading" @change="fetchRuns"
        >
          <el-option v-for="p in pipelines" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-empty v-if="!pipelinesLoading && pipelines.length === 0" description="暂无 Pipeline" :image-size="60" />
      </div>

      <div v-if="runsLoading" class="loading-placeholder">
        <el-icon class="is-loading" :size="32"><Loading /></el-icon>
        <p>加载中...</p>
      </div>
      <template v-else-if="selectedPipeline">
        <el-alert v-if="runs.length === 0" title="该 Pipeline 暂无执行记录" type="info" :closable="false" center />
        <el-table v-else :data="runs" border size="small" style="width: 100%">
          <el-table-column prop="id" label="执行 ID" min-width="200" show-overflow-tooltip>
            <template #default="scope">
              <el-link type="primary" @click="viewRun(scope.row.id)">{{ scope.row.id }}</el-link>
            </template>
          </el-table-column>
          <el-table-column label="触发方式" width="110">
            <template #default="scope">{{ triggerText(scope.row.trigger) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="scope">
              <el-tag size="small" :type="runStatusTag(scope.row.status) as any" effect="dark">
                {{ runStatusText(scope.row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="开始时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.started_at) }}</template>
          </el-table-column>
          <el-table-column label="结束时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.finished_at) }}</template>
          </el-table-column>
          <el-table-column label="错误" min-width="160" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.error || '-' }}</template>
          </el-table-column>
        </el-table>
        <div class="pagination" v-if="total > pageSize">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :total="total"
            layout="total, prev, pager, next, jumper"
            @current-change="onPageChange"
          />
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()

const pipelines = ref<any[]>([])
const pipelinesLoading = ref(false)
const selectedPipeline = ref('')
const runs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const runsLoading = ref(false)

const fetchPipelines = async () => {
  pipelinesLoading.value = true
  try {
    const res: any = await api.get('/pipelines', { limit: 100 })
    pipelines.value = res.pipelines || []
    // 支持 ?pipeline={id} 直达；默认选中第一个
    const fromQuery = String(route.query.pipeline || '')
    if (fromQuery && pipelines.value.some((p) => p.id === fromQuery)) {
      selectedPipeline.value = fromQuery
    } else if (pipelines.value.length > 0) {
      selectedPipeline.value = pipelines.value[0].id
    }
    if (selectedPipeline.value) await fetchRuns()
  } catch (err: any) {
    appStore.error('Pipeline 列表加载失败', err?.message)
  } finally {
    pipelinesLoading.value = false
  }
}

// 执行历史按 Pipeline 查询（后端接口为 /pipelines/{id}/runs）
const fetchRuns = async () => {
  if (!selectedPipeline.value) return
  runsLoading.value = true
  try {
    const res: any = await api.get(`/pipelines/${selectedPipeline.value}/runs`, {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
    })
    runs.value = res.runs || []
    total.value = res.total || 0
  } catch (err: any) {
    appStore.error('执行历史加载失败', err?.message)
  } finally {
    runsLoading.value = false
  }
}

const onPageChange = (p: number) => { page.value = p; fetchRuns() }
const viewRun = (runId: string) => router.push(`/pipelines/run/${selectedPipeline.value}/${runId}`)

const TRIGGER_TEXT: Record<string, string> = {
  manual: '手动', scheduled: '定时', event: '事件', api: 'API',
}
const triggerText = (trigger?: string) => TRIGGER_TEXT[trigger || ''] || trigger || '-'

const RUN_STATUS_TEXT: Record<string, string> = {
  pending: '等待中', running: '运行中', completed: '已完成',
  failed: '失败', cancelled: '已取消', skipped: '已跳过',
}
const runStatusText = (status?: string) => RUN_STATUS_TEXT[status || ''] || status || '-'
const runStatusTag = (status?: string) => {
  const map: Record<string, string> = {
    pending: 'info', running: 'warning', completed: 'success',
    failed: 'danger', cancelled: 'info', skipped: 'info',
  }
  return map[status || ''] || 'info'
}
const formatDate = (ts?: number | null) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

onMounted(fetchPipelines)
</script>

<style scoped>
.page-view { padding: 0; }
.filter-bar { margin-bottom: 16px; }
.pagination { padding: 16px 0 4px; display: flex; justify-content: flex-end; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
