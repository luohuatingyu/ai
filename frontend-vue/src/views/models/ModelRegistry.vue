<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">模型注册表</h1>
      <el-button @click="refreshData">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
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
      <div v-else>
        <el-alert v-if="items.length === 0" title="暂无数据" type="info" :closable="false" center />
        <el-table v-else :data="items" border size="small" style="width: 100%">
          <el-table-column v-for="col in columns" :key="col.prop" :prop="col.prop" :label="col.label" :width="col.width" />
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading, WarningFilled } from '@element-plus/icons-vue'

const appStore = useAppStore()
const items = ref<any[]>([])
const loading = ref(false)
const error = ref('')

const columns = [
  { prop: 'id', label: '模型ID', width: 160 },
  { prop: 'name', label: '名称', width: 150 },
  { prop: 'version', label: '版本', width: 100 },
  { prop: 'task', label: '任务', width: 120 },
  { prop: 'framework', label: '框架', width: 100 },
  { prop: 'status', label: '状态', width: 100 },
]

const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/models')
    items.value = res.jobs || res.models || res.pipelines || res.workers || res.runs || res.deployments || (Array.isArray(res) ? res : [])
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(refreshData)
</script>

<style scoped>
.page-view { padding: 0; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
