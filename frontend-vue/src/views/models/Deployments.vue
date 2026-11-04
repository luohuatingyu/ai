<template>
  <PageShell title="部署管理" module="模型注册表" description="管理模型在线部署实例与访问地址">
    <template #actions>
      <el-button type="primary" plain @click="router.push('/models/registry')">
        <el-icon class="gap-8"><Plus /></el-icon>
        前往模型列表创建部署
      </el-button>
      <el-button @click="refreshData" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
    </template>

    <!-- 筛选 -->
    <FilterBar>
      <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 160px" @change="onSearch">
        <el-option label="排队中" value="pending" />
        <el-option label="运行中" value="running" />
        <el-option label="已停止" value="stopped" />
        <el-option label="失败" value="failed" />
      </el-select>
      <template #actions>
        <span class="text-xs text-secondary">共 {{ formatInteger(total) }} 个部署实例</span>
      </template>
    </FilterBar>

    <!-- 部署表格 -->
    <SectionCard flush>
      <el-table
        v-if="deployments.length > 0"
        v-loading="loading"
        :data="deployments"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="id" label="部署 ID" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono text-sm">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="模型" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <el-link type="primary" underline="never" @click="viewModel(row.model_id)">
              {{ row.model_name }} <span class="text-xs text-ink-400">v{{ row.model_version }}</span>
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="endpoint_name" label="端点名称" width="130">
          <template #default="{ row }">{{ row.endpoint_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <StatusTag :status="row.status" :map="DEPLOY_STATUS" />
          </template>
        </el-table-column>
        <el-table-column label="副本数" width="80">
          <template #default="{ row }">{{ row.config?.replicas ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="120">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" link type="primary" @click="viewModel(row.model_id)">模型详情</el-button>
              <el-button
                v-if="row.status !== 'stopped'"
                size="small" link type="danger" @click="stopDeployment(row)">停止</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else-if="error"
        type="error"
        title="部署记录加载失败"
        :description="error"
        retryable
        @retry="refreshData"
      />
      <EmptyState
        v-else-if="statusFilter"
        title="当前筛选条件下没有部署"
        description="切换状态筛选或查看全部部署"
      >
        <template #actions>
          <el-button size="small" @click="statusFilter = ''; onSearch()">查看全部</el-button>
        </template>
      </EmptyState>
      <EmptyState
        v-else
        title="暂无部署记录"
        description="可在模型详情页对已注册模型执行「部署服务」"
      >
        <template #actions>
          <el-button type="primary" size="small" @click="router.push('/models/registry')">前往模型列表</el-button>
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
 * 部署管理：全局部署实例列表，支持状态筛选与停止操作。
 * - 停止部署带确认弹窗（展示部署 ID），停止后端点不再对外服务；
 * - 部署管理页读取全局部署记录接口（旧版误请求 /models 把模型列表当部署展示）。
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import FilterBar from '@/components/common/FilterBar.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { DEPLOY_STATUS } from '@/utils/status'
import { formatDateTime, formatInteger } from '@/utils/format'
import { Refresh, Plus } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()
const deployments = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const statusFilter = ref('')
const loading = ref(false)
const error = ref('')

// 部署管理页读取全局部署记录接口
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
    appStore.error('部署记录加载失败', err?.message)
  } finally {
    loading.value = false
  }
}

const onSearch = () => {
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

const stopDeployment = async (deployment: any) => {
  try {
    await ElMessageBox.confirm(
      `确认停止部署 ${deployment.id}？停止后端点不再对外提供推理。`,
      '停止确认',
      { confirmButtonText: '停止', cancelButtonText: '取消', type: 'warning' }
    )
    await api.delete(`/models/deployments/${deployment.id}`)
    appStore.success('部署已停止', deployment.model_name)
    refreshData()
  } catch (err: any) {
    if (err !== 'cancel') appStore.error('停止失败', err?.message)
  }
}

onMounted(refreshData)
</script>
