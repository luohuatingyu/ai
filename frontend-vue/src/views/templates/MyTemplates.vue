<template>
  <PageShell title="我的模板" module="脚本模板库" description="管理自建脚本模板：编辑、使用与删除">
    <template #actions>
      <el-button @click="fetchTemplates" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button type="primary" @click="router.push('/templates/create')">
        <el-icon class="gap-8"><Plus /></el-icon>
        创建模板
      </el-button>
    </template>

    <SectionCard flush>
      <el-table
        v-if="templates.length > 0"
        v-loading="loading"
        :data="templates"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="name" label="模板名称" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" underline="never" @click="router.push(`/templates/detail/${row.id}`)">
              {{ row.name }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="任务类型" width="110">
          <template #default="{ row }">
            <StatusTag :status="row.task_type" :map="TASK_TYPE" />
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column label="参数数" width="80">
          <template #default="{ row }">{{ row.parameters?.length || 0 }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="130">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" link type="primary" @click="router.push(`/templates/detail/${row.id}`)">查看</el-button>
              <el-button size="small" link type="primary" @click="router.push(`/templates/create?id=${row.id}`)">编辑</el-button>
              <el-button size="small" link type="success" @click="router.push(`/training/create?template=${row.id}`)">使用</el-button>
              <el-popconfirm
                title="确认删除该模板？删除后不可恢复。"
                confirm-button-text="删除"
                cancel-button-text="取消"
                @confirm="deleteTemplate(row.id)"
              >
                <template #reference>
                  <el-button size="small" link type="danger">删除</el-button>
                </template>
              </el-popconfirm>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState
        v-else-if="error"
        type="error"
        title="模板加载失败"
        :description="error"
        retryable
        @retry="fetchTemplates"
      />
      <EmptyState
        v-else-if="!loading"
        title="暂无用户模板"
        description="创建脚本模板后可复用于训练任务"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="router.push('/templates/create')">创建第一个模板</el-button>
        </template>
      </EmptyState>
      <div v-else v-loading="true" class="loading-placeholder" />
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 我的模板：仅展示用户自建模板（is_builtin=false），表格化管理。
 * - 行内操作：查看 / 编辑 / 使用（跳转训练创建页）/ 删除（Popconfirm 确认）；
 * - 失败展示可重试错误态。
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { TASK_TYPE } from '@/utils/status'
import { formatDateTime } from '@/utils/format'
import { Refresh, Plus } from '@element-plus/icons-vue'

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
    appStore.error('模板加载失败', err?.message)
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

onMounted(fetchTemplates)
</script>

<style scoped>
.loading-placeholder {
  min-height: 200px;
}
</style>
