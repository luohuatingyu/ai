<template>
  <PageShell title="数据集列表" module="数据集管理" description="统一浏览、检索与管理平台数据集，支持预览、统计与预处理">
    <template #actions>
      <el-button @click="fetchDatasets" :loading="loading">
        <el-icon class="gap-8"><Refresh /></el-icon>
        刷新
      </el-button>
      <el-button @click="router.push('/datasets/huggingface')">
        <el-icon class="gap-8"><Link /></el-icon>
        HuggingFace 导入
      </el-button>
      <el-button type="primary" @click="router.push('/datasets/import')">
        <el-icon class="gap-8"><Upload /></el-icon>
        导入数据集
      </el-button>
    </template>

    <!-- 筛选区：搜索 + 标签；回车或点击搜索按钮触发查询 -->
    <FilterBar>
      <el-input
        v-model="searchForm.search"
        placeholder="搜索名称 / 描述 / 列名"
        clearable
        style="width: 240px"
        :prefix-icon="Search"
        @keyup.enter="onSearch"
        @clear="onSearch"
      />
      <el-select
        v-model="searchForm.tags"
        multiple
        collapse-tags
        collapse-tags-tooltip
        placeholder="按标签筛选"
        style="width: 220px"
      >
        <el-option v-for="tag in allTags" :key="tag" :label="tag" :value="tag" />
      </el-select>
      <el-button type="primary" plain @click="onSearch">搜索</el-button>
      <el-button @click="resetSearch">重置</el-button>
      <template #actions>
        <span class="text-xs text-secondary">共 {{ formatInteger(total) }} 个数据集</span>
      </template>
    </FilterBar>

    <!-- 数据集表格 -->
    <SectionCard flush>
      <el-table
        v-loading="loading"
        :data="datasets"
        class="table-card"
        :header-cell-style="{ background: 'var(--mtp-bg-subtle)' }"
      >
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="ds-name">
              <span class="ds-name__title">{{ row.name }}</span>
              <span class="ds-name__id">{{ row.id }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="120">
          <template #default="{ row }">
            <StatusTag :status="row.source" :map="DATASET_SOURCE" />
          </template>
        </el-table-column>
        <el-table-column label="规模" width="150">
          <template #default="{ row }">
            <span class="mono">{{ formatInteger(row.rows) }}</span> 行 ×
            <span class="mono">{{ row.columns?.length || 0 }}</span> 列
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="160">
          <template #default="{ row }">
            <template v-if="row.tags?.length">
              <el-tag v-for="tag in row.tags" :key="tag" size="small" effect="plain" class="mr-4">{{ tag }}</el-tag>
            </template>
            <span v-else class="text-ink-400">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="130">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" size="small" @click="viewDataset(row)">
                <el-icon><View /></el-icon>预览
              </el-button>
              <el-button link type="primary" size="small" @click="viewStats(row)">
                <el-icon><DataAnalysis /></el-icon>统计
              </el-button>
              <el-dropdown trigger="click" @command="(cmd: string) => onRowCommand(cmd, row)">
                <el-button link size="small">
                  更多<el-icon><ArrowDown /></el-icon>
                </el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="process">
                      <el-icon><Setting /></el-icon>预处理
                    </el-dropdown-item>
                    <el-dropdown-item v-if="row.source === 'huggingface'" command="materialize">
                      <el-icon><Download /></el-icon>下载到本地
                    </el-dropdown-item>
                    <el-dropdown-item command="delete" divided>
                      <el-icon style="color: var(--mtp-danger)"><Delete /></el-icon>
                      <span style="color: var(--mtp-danger)">删除</span>
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
        <template #empty>
          <!-- 首次加载失败 → 可重试错误态；有筛选条件 → 无匹配；否则 → 引导导入 -->
          <EmptyState
            v-if="loadError"
            type="error"
            title="数据集加载失败"
            :description="loadError"
            retryable
            @retry="fetchDatasets"
          />
          <EmptyState
            v-else-if="hasFilter"
            title="没有匹配的数据集"
            description="调整搜索关键词或标签筛选后重试"
          >
            <template #actions>
              <el-button size="small" @click="resetSearch">清除筛选</el-button>
            </template>
          </EmptyState>
          <EmptyState v-else title="暂无数据集" description="导入本地文件或从 HuggingFace 拉取数据集开始使用">
            <template #actions>
              <el-button type="primary" size="small" @click="router.push('/datasets/import')">导入数据集</el-button>
              <el-button size="small" @click="router.push('/datasets/huggingface')">HuggingFace 导入</el-button>
            </template>
          </EmptyState>
        </template>
      </el-table>

      <div class="table-pagination" v-if="total > 0">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="onPageSizeChange"
          @current-change="onPageChange"
        />
      </div>
    </SectionCard>

    <!-- 预览抽屉：数据抽样浏览 -->
    <el-drawer v-model="showPreviewDrawer" :title="`数据预览 · ${previewDatasetName}`" size="72%" destroy-on-close>
      <div v-loading="previewLoading" class="preview-body">
        <template v-if="previewData">
          <div class="preview-meta mb-12">
            <el-tag effect="plain" size="small">{{ formatInteger(previewData.rows) }} 行</el-tag>
            <el-tag effect="plain" size="small">{{ previewData.columns?.length || 0 }} 列</el-tag>
            <el-tag effect="plain" size="small">第 {{ previewPage }} / {{ Math.max(1, Math.ceil(previewData.rows / previewPageSize)) }} 页</el-tag>
          </div>
          <el-table :data="previewData.preview" class="table-card" border size="small" max-height="560">
            <el-table-column
              v-for="col in previewData.columns"
              :key="col"
              :prop="col"
              :label="col"
              min-width="120"
              show-overflow-tooltip
            />
          </el-table>
          <div class="table-pagination">
            <el-pagination
              v-model:current-page="previewPage"
              :page-size="previewPageSize"
              :total="previewData.rows"
              layout="prev, pager, next, jumper"
              @current-change="fetchPreview"
            />
          </div>
        </template>
        <EmptyState v-else-if="previewError" type="error" title="预览加载失败" :description="previewError" retryable @retry="fetchPreview" />
      </div>
    </el-drawer>

    <!-- 统计抽屉：概览 + 列详情 -->
    <el-drawer v-model="showStatsDrawer" :title="`统计信息 · ${statsDatasetName}`" size="62%" destroy-on-close>
      <div v-if="statsData">
        <el-tabs v-model="statsTab">
          <el-tab-pane label="概览" name="overview">
            <el-descriptions :column="3" border>
              <el-descriptions-item label="总行数">{{ formatInteger(statsData.total_rows) }}</el-descriptions-item>
              <el-descriptions-item label="总列数">{{ statsData.total_columns }}</el-descriptions-item>
              <el-descriptions-item label="内存占用">{{ (statsData.memory_usage_mb || 0).toFixed(2) }} MB</el-descriptions-item>
            </el-descriptions>
          </el-tab-pane>
          <el-tab-pane label="列详情" name="columns">
            <el-table :data="columnStats" class="table-card" border size="small" max-height="520">
              <el-table-column prop="column" label="列名" min-width="120" show-overflow-tooltip />
              <el-table-column prop="type" label="类型" width="100" />
              <el-table-column prop="null_count" label="空值数" width="90" />
              <el-table-column prop="unique_count" label="唯一值" width="90" />
              <el-table-column prop="min" label="最小值" width="100" show-overflow-tooltip />
              <el-table-column prop="max" label="最大值" width="100" show-overflow-tooltip />
              <el-table-column prop="mean" label="平均值" width="100" show-overflow-tooltip />
              <el-table-column prop="std" label="标准差" width="100" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
      <EmptyState v-else-if="statsError" type="error" title="统计加载失败" :description="statsError" retryable @retry="retryStats" />
      <div v-else v-loading="true" class="stats-loading" />
    </el-drawer>

    <!-- 预处理对话框：操作类型 + JSON 参数（带校验） -->
    <el-dialog v-model="showProcessDialog" title="数据预处理" width="640px" destroy-on-close>
      <el-alert
        v-if="processForm.datasetName"
        :title="`目标数据集：${processForm.datasetName}`"
        type="info"
        :closable="false"
        class="mb-12"
        show-icon
      />
      <el-form label-width="100px">
        <el-form-item label="操作类型" required>
          <el-select v-model="processForm.operation_type" placeholder="选择预处理操作" style="width: 100%">
            <el-option label="去除重复行" value="remove_duplicates" />
            <el-option label="填充缺失值" value="fill_missing" />
            <el-option label="数值归一化" value="normalize" />
            <el-option label="分类变量编码" value="encode_categorical" />
            <el-option label="去除异常值" value="remove_outliers" />
            <el-option label="选择列" value="select_columns" />
            <el-option label="过滤行" value="filter_rows" />
            <el-option label="列转换" value="transform_column" />
          </el-select>
        </el-form-item>
        <el-form-item label="参数配置" :error="processParamsError">
          <el-input
            v-model="processForm.params_json"
            type="textarea"
            :rows="6"
            placeholder='JSON 格式参数，如 {"columns": ["age"]}'
            class="mono"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showProcessDialog = false">取消</el-button>
        <el-button type="primary" :loading="processing" @click="submitProcess">执行</el-button>
      </template>
    </el-dialog>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 数据集列表：筛选检索、规模总览、表格管理与快速操作。
 * - 预览与统计使用抽屉（避免整页跳转），预处理保留对话框；
 * - 导入入口统一跳转到独立页面（/datasets/import、/datasets/huggingface），
 *   本页不再重复放置导入表单；
 * - 空态区分"加载失败 / 筛选无结果 / 完全无数据"三种情形。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import FilterBar from '@/components/common/FilterBar.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { DATASET_SOURCE } from '@/utils/status'
import { formatDateTime, formatInteger, tryParseJSON } from '@/utils/format'
import {
  Refresh, Upload, Link, View, DataAnalysis, Setting, Download, Delete, ArrowDown, Search,
} from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

// ---- 列表状态 ----
const loading = ref(false)
const loadError = ref('')
const datasets = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const searchForm = reactive({ search: '', tags: [] as string[] })
const allTags = ref<string[]>([])

/** 是否处于筛选状态（决定空态文案） */
const hasFilter = computed(() => Boolean(searchForm.search) || searchForm.tags.length > 0)

// ---- 预览 ----
const showPreviewDrawer = ref(false)
const previewLoading = ref(false)
const previewError = ref('')
const previewData = ref<any>(null)
const previewDatasetName = ref('')
const previewDatasetId = ref('')
const previewPage = ref(1)
const previewPageSize = ref(50)

// ---- 统计 ----
const showStatsDrawer = ref(false)
const statsError = ref('')
const statsData = ref<any>(null)
const statsDatasetName = ref('')
const statsDatasetId = ref('')
const statsTab = ref('overview')
const columnStats = ref<any[]>([])

// ---- 预处理 ----
const showProcessDialog = ref(false)
const processing = ref(false)
const processParamsError = ref('')
const processForm = reactive({ datasetId: '', datasetName: '', operation_type: '', params_json: '{}' })

/** 拉取数据集列表（带筛选与分页） */
const fetchDatasets = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const res: any = await api.get('/datasets', {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      search: searchForm.search || undefined,
      tags: searchForm.tags.length > 0 ? searchForm.tags : undefined,
    })
    datasets.value = res.datasets || []
    total.value = res.total || 0
  } catch (err: any) {
    loadError.value = err?.message || '网络错误'
    appStore.error('加载失败', err?.message)
  } finally {
    loading.value = false
  }
}

/** 拉取可选标签（失败静默：标签筛选退化为手工输入场景） */
const fetchTags = async () => {
  try {
    const res: any = await api.get('/datasets/tags')
    allTags.value = res.tags || []
  } catch (err) {
    console.error('获取标签失败', err)
  }
}

/** 搜索：回到第一页再查询 */
const onSearch = () => {
  page.value = 1
  fetchDatasets()
}

const resetSearch = () => {
  searchForm.search = ''
  searchForm.tags = []
  page.value = 1
  fetchDatasets()
}

const onPageChange = (p: number) => {
  page.value = p
  fetchDatasets()
}

const onPageSizeChange = (size: number) => {
  pageSize.value = size
  page.value = 1
  fetchDatasets()
}

// ---- 行操作分发 ----
const onRowCommand = (command: string, row: any) => {
  if (command === 'process') openProcess(row)
  else if (command === 'materialize') materializeDataset(row)
  else if (command === 'delete') deleteDataset(row)
}

// ---- 预览 ----
const viewDataset = (row: any) => {
  previewDatasetId.value = row.id
  previewDatasetName.value = row.name || row.id
  previewData.value = null
  previewError.value = ''
  previewPage.value = 1
  showPreviewDrawer.value = true
  fetchPreview()
}

const fetchPreview = async () => {
  if (!previewDatasetId.value) return
  previewLoading.value = true
  previewError.value = ''
  try {
    const res: any = await api.get(`/datasets/${previewDatasetId.value}`, {
      rows: previewPageSize.value,
      offset: (previewPage.value - 1) * previewPageSize.value,
    })
    previewData.value = res
  } catch (err: any) {
    previewError.value = err?.message || '网络错误'
    appStore.error('预览失败', err?.message)
  } finally {
    previewLoading.value = false
  }
}

// ---- 统计 ----
const viewStats = async (row: any) => {
  statsDatasetId.value = row.id
  statsDatasetName.value = row.name || row.id
  statsData.value = null
  statsError.value = ''
  statsTab.value = 'overview'
  showStatsDrawer.value = true
  await loadStats()
}

const loadStats = async () => {
  if (!statsDatasetId.value) return
  try {
    const res: any = await api.get(`/datasets/${statsDatasetId.value}/stats`)
    statsData.value = res
    columnStats.value = Object.entries(res.column_stats || {}).map(([col, stat]: [string, any]) => ({
      column: col,
      ...stat,
    }))
  } catch (err: any) {
    statsError.value = err?.message || '网络错误'
    appStore.error('获取统计失败', err?.message)
  }
}

const retryStats = () => loadStats()

// ---- 预处理 ----
const openProcess = (row: any) => {
  processForm.datasetId = row.id
  processForm.datasetName = row.name || row.id
  processForm.operation_type = ''
  processForm.params_json = '{}'
  processParamsError.value = ''
  showProcessDialog.value = true
}

const submitProcess = async () => {
  // 前置校验：操作类型必选、参数必须为合法 JSON 对象
  if (!processForm.operation_type) {
    appStore.warning('请选择预处理操作')
    return
  }
  const params = tryParseJSON(processForm.params_json)
  if (params === undefined) {
    processParamsError.value = '参数必须是合法的 JSON 对象，如 {"columns": ["age"]}'
    return
  }
  processParamsError.value = ''
  processing.value = true
  try {
    await api.post(`/datasets/${processForm.datasetId}/process`, {
      operations: [{ type: processForm.operation_type, ...params }],
    })
    appStore.success('预处理完成')
    showProcessDialog.value = false
    fetchDatasets()
  } catch (err: any) {
    appStore.error('预处理失败', err?.message)
  } finally {
    processing.value = false
  }
}

// ---- 删除（二次确认展示数据集名称，防止误删） ----
const deleteDataset = async (row: any) => {
  const name = row.name || row.id
  try {
    await ElMessageBox.confirm(
      `确认删除数据集「${name}」？删除后本地文件、对象存储副本与数据库镜像将一并清理，不可恢复。`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await api.delete(`/datasets/${row.id}`)
    appStore.success('删除成功', name)
    fetchDatasets()
  } catch (err: any) {
    appStore.error('删除失败', err?.message)
  }
}

// ---- 下载到本地（仅 HuggingFace 来源） ----
const materializeDataset = async (row: any) => {
  try {
    await api.post(`/datasets/${row.id}/materialize`)
    appStore.success('下载完成', row.name || row.id)
    fetchDatasets()
  } catch (err: any) {
    appStore.error('下载失败', err?.message)
  }
}

onMounted(() => {
  fetchDatasets()
  fetchTags()
})
</script>

<style scoped>
.ds-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ds-name__title {
  font-weight: 500;
  color: var(--mtp-ink-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ds-name__id {
  font-size: 11px;
  color: var(--mtp-ink-400);
  font-family: var(--mtp-font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mr-4 {
  margin-right: 4px;
  margin-bottom: 2px;
}

.preview-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.stats-loading {
  min-height: 200px;
}
</style>
