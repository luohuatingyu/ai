<template>
  <div class="page-view">
    <div class="page-header">
      <h1 class="page-title">我的模板</h1>
      <div class="header-actions">
        <el-button type="primary" @click="router.push('/templates/create')">
          <el-icon><Plus /></el-icon>
          创建模板
        </el-button>
        <el-button @click="fetchTemplates" :loading="loading">
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
        <el-button type="primary" @click="fetchTemplates">重试</el-button>
      </div>
      <template v-else>
        <el-alert v-if="templates.length === 0" title="暂无用户模板，点击右上角创建" type="info" :closable="false" center />
        <el-table v-else :data="templates" border size="small" style="width: 100%">
          <el-table-column prop="name" label="模板名称" min-width="160" show-overflow-tooltip />
          <el-table-column prop="category" label="分类" width="120">
            <template #default="scope">
              <el-tag size="small">{{ scope.row.category }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="任务类型" width="120">
            <template #default="scope">{{ taskText(scope.row.task_type) }}</template>
          </el-table-column>
          <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
            <template #default="scope">{{ scope.row.description || '-' }}</template>
          </el-table-column>
          <el-table-column label="参数数" width="80">
            <template #default="scope">{{ scope.row.parameters?.length || 0 }}</template>
          </el-table-column>
          <el-table-column label="更新时间" width="160">
            <template #default="scope">{{ formatDate(scope.row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="scope">
              <el-button size="small" link type="primary" @click="router.push(`/templates/detail/${scope.row.id}`)">查看</el-button>
              <el-button size="small" link type="primary" @click="router.push(`/templates/create?id=${scope.row.id}`)">编辑</el-button>
              <el-button size="small" link type="success" @click="router.push(`/training/create?template=${scope.row.id}`)">使用</el-button>
              <el-popconfirm title="确认删除该模板？" confirm-button-text="删除" cancel-button-text="取消" @confirm="deleteTemplate(scope.row.id)">
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
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import { Refresh, Loading, WarningFilled, Plus } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const templates = ref<any[]>([])
const loading = ref(false)
const error = ref('')

// 仅拉取用户模板（is_builtin=false）
const fetchTemplates = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/templates', { is_builtin: false, limit: 100 })
    templates.value = res.templates || []
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

const deleteTemplate = async (id: string) => {
  try {
    await api.delete(`/templates/${id}`)
    appStore.success('模板已删除')
    fetchTemplates()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

const TASK_TEXT: Record<string, string> = {
  classification: '分类', regression: '回归', clustering: '聚类', time_series: '时间序列',
}
const taskText = (task?: string) => TASK_TEXT[task || ''] || task || '-'
const formatDate = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

onMounted(fetchTemplates)
</script>

<style scoped>
.page-view { padding: 0; }
.header-actions { display: flex; gap: 12px; }
.loading-placeholder { text-align: center; padding: 60px; color: #909399; }
</style>
