<template>
  <PageShell title="模板库" module="脚本模板库" description="按任务类型浏览内置脚本模板，一键用于训练任务">
    <template #actions>
      <el-button type="primary" @click="router.push('/templates/create')">
        <el-icon class="gap-8"><Plus /></el-icon>
        创建模板
      </el-button>
    </template>

    <!-- 筛选区 -->
    <FilterBar>
      <el-select v-model="selectedCategory" placeholder="全部分类" clearable style="width: 180px" @change="onSearch">
        <el-option v-for="cat in categories" :key="cat.name" :label="cat.name" :value="cat.name" />
      </el-select>
      <el-select v-model="showBuiltin" placeholder="全部来源" clearable style="width: 150px" @change="onSearch">
        <el-option label="内置模板" value="true" />
        <el-option label="用户模板" value="false" />
      </el-select>
      <el-input
        v-model="searchKeyword"
        placeholder="搜索模板名称 / 描述"
        clearable
        style="width: 240px"
        :prefix-icon="Search"
        @keyup.enter="onSearch"
        @clear="onSearch"
      />
      <el-button type="primary" plain @click="onSearch">搜索</el-button>
      <template #actions>
        <span class="text-xs text-secondary">共 {{ formatInteger(total) }} 个模板</span>
      </template>
    </FilterBar>

    <!-- 模板网格 -->
    <div v-loading="loading" class="library-body">
      <div v-if="templates.length > 0" class="template-grid">
        <div v-for="template in templates" :key="template.id" class="template-card">
          <div class="template-card__head" @click="viewTemplate(template.id)">
            <span class="template-card__category">{{ template.category }}</span>
            <el-tag v-if="template.is_builtin" size="small" effect="dark" disable-transitions>内置</el-tag>
          </div>
          <h3 class="template-card__name" @click="viewTemplate(template.id)">{{ template.name }}</h3>
          <p class="template-card__desc">{{ template.description || '暂无描述' }}</p>
          <div class="template-card__meta">
            <StatusTag :status="template.task_type" :map="TASK_TYPE" />
            <span class="text-xs text-ink-400">{{ template.parameters?.length || 0 }} 个参数</span>
          </div>
          <div class="template-card__params">
            <span v-for="p in (template.parameters || []).slice(0, 5)" :key="p.name" class="param-chip">{{ p.label }}</span>
            <span v-if="(template.parameters || []).length > 5" class="param-chip param-chip--more">+{{ template.parameters.length - 5 }}</span>
          </div>
          <div class="template-card__footer">
            <el-button size="small" type="primary" @click="useTemplate(template)">
              <el-icon><VideoPlay /></el-icon>
              使用模板
            </el-button>
            <el-button size="small" @click="viewTemplate(template.id)">
              <el-icon><View /></el-icon>
              详情
            </el-button>
            <el-popconfirm
              v-if="!template.is_builtin"
              title="确认删除该模板？删除后不可恢复。"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="deleteTemplate(template.id)"
            >
              <template #reference>
                <el-button size="small" type="danger" plain>删除</el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </div>

      <!-- 三态空态：加载失败 / 筛选无结果 / 完全无模板 -->
      <SectionCard v-else-if="!loading" flush>
        <EmptyState
          v-if="loadError"
          type="error"
          title="模板加载失败"
          :description="loadError"
          retryable
          @retry="fetchTemplates"
        />
        <EmptyState
          v-else-if="hasFilter"
          title="没有匹配的模板"
          description="调整分类、来源或搜索关键词后重试"
        >
          <template #actions>
            <el-button size="small" @click="resetFilters">清除筛选</el-button>
          </template>
        </EmptyState>
        <EmptyState v-else title="暂无模板" description="创建第一个脚本模板，或直接使用训练任务页内置配置">
          <template #actions>
            <el-button type="primary" size="small" @click="router.push('/templates/create')">创建第一个模板</el-button>
          </template>
        </EmptyState>
      </SectionCard>
      <div v-else class="loading-placeholder" />
    </div>

    <!-- 分页 -->
    <div class="table-pagination" v-if="total > pageSize">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[12, 24, 48]"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="onPageSizeChange"
        @current-change="onPageChange"
      />
    </div>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 模板库：分类/来源/关键词三维筛选 + 模板卡片网格。
 * - 内置模板不可删除，用户模板删除带 Popconfirm 二次确认；
 * - 使用模板跳转训练创建页并携带模板 ID，由创建页拉取参数预填；
 * - 空态区分"加载失败 / 筛选无结果 / 完全无数据"。
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import FilterBar from '@/components/common/FilterBar.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { TASK_TYPE } from '@/utils/status'
import { formatInteger } from '@/utils/format'
import { Plus, VideoPlay, View, Search } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

const templates = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(12)
const selectedCategory = ref('')
const showBuiltin = ref('')
const searchKeyword = ref('')
const categories = ref<any[]>([])
const loading = ref(false)
const loadError = ref('')

/** 是否处于筛选状态（决定空态文案） */
const hasFilter = computed(() => Boolean(selectedCategory.value) || Boolean(showBuiltin.value) || Boolean(searchKeyword.value))

const fetchTemplates = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const res: any = await api.get('/templates', {
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      category: selectedCategory.value || undefined,
      is_builtin: showBuiltin.value === '' ? undefined : showBuiltin.value === 'true',
      search: searchKeyword.value || undefined,
    })
    templates.value = res.templates || []
    total.value = res.total || 0
  } catch (err: any) {
    loadError.value = err?.message || '网络错误'
    appStore.error('加载失败', err?.message)
  } finally {
    loading.value = false
  }
}

const fetchCategories = async () => {
  try {
    const res: any = await api.get('/templates/categories')
    categories.value = res.categories || []
  } catch (err) {
    console.error('获取分类失败', err)
  }
}

/** 搜索：回到第一页再查询 */
const onSearch = () => {
  page.value = 1
  fetchTemplates()
}

const resetFilters = () => {
  selectedCategory.value = ''
  showBuiltin.value = ''
  searchKeyword.value = ''
  page.value = 1
  fetchTemplates()
}

const onPageChange = (p: number) => {
  page.value = p
  fetchTemplates()
}

const onPageSizeChange = (size: number) => {
  pageSize.value = size
  page.value = 1
  fetchTemplates()
}

const viewTemplate = (id: string) => {
  router.push(`/templates/detail/${id}`)
}

/** 使用模板：携带模板 ID 跳转创建任务页，由创建页拉取模板并预填参数 */
const useTemplate = (template: any) => {
  router.push(`/training/create?template=${template.id}`)
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

onMounted(() => {
  fetchTemplates()
  fetchCategories()
})
</script>

<style scoped>
.library-body {
  min-height: 200px;
}

.loading-placeholder {
  min-height: 200px;
}

.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 14px;
}

.template-card {
  background: var(--mtp-bg-card);
  border: 1px solid var(--mtp-ink-100);
  border-radius: var(--mtp-radius-lg);
  box-shadow: var(--mtp-shadow-card);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: box-shadow 0.2s, transform 0.2s;
}

.template-card:hover {
  box-shadow: var(--mtp-shadow-card-hover);
  transform: translateY(-2px);
}

.template-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.template-card__category {
  font-size: 12px;
  color: var(--mtp-brand-600);
  font-weight: 500;
  background: var(--mtp-brand-100);
  padding: 2px 10px;
  border-radius: 999px;
}

.template-card__name {
  font-size: 15px;
  font-weight: 600;
  margin: 2px 0 0;
  color: var(--mtp-ink-900);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-card__name:hover {
  color: var(--mtp-brand-600);
}

.template-card__desc {
  color: var(--mtp-ink-500);
  font-size: 13px;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  flex: 1;
  min-height: 38px;
}

.template-card__meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.template-card__params {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.param-chip {
  font-size: 11px;
  padding: 2px 8px;
  background: var(--mtp-bg-subtle);
  border-radius: 4px;
  color: var(--mtp-ink-700);
}

.param-chip--more {
  background: var(--mtp-ink-100);
  color: var(--mtp-ink-500);
}

.template-card__footer {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px solid var(--mtp-ink-100);
}

.template-card__footer .el-button + .el-button {
  margin-left: 0;
}
</style>
