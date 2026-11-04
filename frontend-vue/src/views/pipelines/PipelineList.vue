<template>
  <PageShell title="Pipeline 列表" module="Pipeline 编排" description="管理多步骤训练流水线：编辑、执行与调度">
    <template #actions>
      <el-button type="primary" @click="router.push('/pipelines/create')">
        <el-icon class="gap-8"><Plus /></el-icon>
        创建 Pipeline
      </el-button>
      <el-button @click="refreshData" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
    </template>

    <!-- 状态筛选（客户端过滤） -->
    <FilterBar>
      <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 160px">
        <el-option label="草稿" value="draft" />
        <el-option label="已激活" value="active" />
        <el-option label="已暂停" value="paused" />
        <el-option label="已归档" value="archived" />
      </el-select>
      <template #actions>
        <span class="text-xs text-secondary">共 {{ pipelines.length }} 个 Pipeline{{ statusFilter ? `，当前筛选 ${filteredPipelines.length} 个` : '' }}</span>
      </template>
    </FilterBar>

    <!-- Pipeline 表格 -->
    <SectionCard flush>
      <el-table
        v-if="filteredPipelines.length > 0"
        v-loading="loading"
        :data="filteredPipelines"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="name" label="名称" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" underline="never" @click="viewDetail(row.id)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column prop="steps_count" label="步骤数" width="80">
          <template #default="{ row }">{{ row.steps_count ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="调度" width="130">
          <template #default="{ row }">
            <span v-if="row.schedule" class="mono text-sm">{{ row.schedule }}</span>
            <span v-else class="text-ink-400">手动</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <StatusTag :status="row.status" :map="PIPELINE_STATUS" />
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="120">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button
                v-if="row.status === 'active'"
                size="small" link type="success" @click="runPipeline(row)">运行</el-button>
              <el-button size="small" link type="primary" @click="router.push(`/pipelines/runs?pipeline=${row.id}`)">执行历史</el-button>
              <el-button size="small" link type="primary" @click="router.push(`/pipelines/create?id=${row.id}`)">编辑</el-button>
              <el-popconfirm title="确认删除该 Pipeline？执行历史将一并删除。" confirm-button-text="删除" cancel-button-text="取消" @confirm="deletePipeline(row)">
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
        title="Pipeline 加载失败"
        :description="error"
        retryable
        @retry="refreshData"
      />
      <EmptyState
        v-else-if="statusFilter"
        title="当前筛选条件下没有 Pipeline"
        description="切换状态筛选或查看全部"
      >
        <template #actions>
          <el-button size="small" @click="statusFilter = ''">查看全部</el-button>
        </template>
      </EmptyState>
      <EmptyState v-else title="暂无 Pipeline" description="创建第一个多步骤流水线，串联数据、训练与部署">
        <template #actions>
          <el-button type="primary" size="small" @click="router.push('/pipelines/create')">创建 Pipeline</el-button>
        </template>
      </EmptyState>
    </SectionCard>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * Pipeline 列表：状态筛选（客户端）+ 流水线表格。
 * - 仅已激活的 Pipeline 可立即运行（确认后跳转执行详情）；
 * - 删除带 Popconfirm 确认（提示执行历史一并删除）。
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
import { PIPELINE_STATUS } from '@/utils/status'
import { formatDateTime } from '@/utils/format'
import { Refresh, Plus } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const pipelines = ref<any[]>([])
const loading = ref(false)
const error = ref('')
const statusFilter = ref('')

const filteredPipelines = computed(() => {
  if (!statusFilter.value) return pipelines.value
  return pipelines.value.filter((p) => p.status === statusFilter.value)
})

const refreshData = async () => {
  loading.value = true
  error.value = ''
  try {
    const res: any = await api.get('/pipelines', { limit: 100 })
    pipelines.value = res.pipelines || []
  } catch (err: any) {
    error.value = err?.message || '加载失败'
    appStore.error('Pipeline 加载失败', err?.message)
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

const deletePipeline = async (pipeline: any) => {
  try {
    await api.delete(`/pipelines/${pipeline.id}`)
    appStore.success('Pipeline 已删除', pipeline.name)
    refreshData()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

onMounted(refreshData)
</script>
