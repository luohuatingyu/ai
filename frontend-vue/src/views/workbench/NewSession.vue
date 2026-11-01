<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">新建会话</h1>
      <div class="header-actions">
        <el-button @click="refreshData">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <slot name="actions" />
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
      <div v-else class="view-content">
        <slot />
        <el-alert v-if="items.length === 0" title="暂无数据" type="info" :closable="false" center />
        <el-table v-else :data="items" border size="small" style="width: 100%">
          <el-table-column v-for="col in columns" :key="col.prop" :prop="col.prop" :label="col.label" :width="col.width">
            <template #default="scope" v-if="col.formatter">
              {{ col.formatter(scope.row[col.prop]) }}
            </template>
          </el-table-column>
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
const columns: any[] = []
const loading = ref(false)
const error = ref('')

const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/workbench/sessions')
    items.value = res.datasets || res.jobs || res.models || res.pipelines || res.workers || res.sessions || res.templates || res.runs || (Array.isArray(res) ? res : [])
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
