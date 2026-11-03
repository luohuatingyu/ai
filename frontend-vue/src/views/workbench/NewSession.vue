<template>
  <PageShell title="新建会话" module="交互式工作台" description="选择运行环境与计算资源，创建新的 Notebook 会话">
    <template #actions>
      <el-button @click="router.push('/workbench/sessions')">
        <el-icon class="gap-8"><Back /></el-icon>
        返回会话列表
      </el-button>
    </template>

    <div class="create-grid">
      <!-- 配置表单 -->
      <div class="create-main">
        <SectionCard title="基本信息">
          <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
            <el-form-item label="会话名称" prop="name">
              <el-input v-model="form.name" placeholder="如：销量预测实验" maxlength="80" show-word-limit />
            </el-form-item>
            <el-form-item label="内核类型" prop="kernel_type">
              <div class="kernel-options">
                <button
                  v-for="option in KERNEL_OPTIONS"
                  :key="option.value"
                  type="button"
                  class="kernel-option"
                  :class="{ 'is-active': form.kernel_type === option.value }"
                  @click="form.kernel_type = option.value"
                >
                  <el-icon :size="18"><component :is="option.icon" /></el-icon>
                  <span class="kernel-option__name">{{ option.label }}</span>
                  <span class="kernel-option__desc">{{ option.desc }}</span>
                </button>
              </div>
            </el-form-item>
          </el-form>
        </SectionCard>

        <SectionCard title="关联数据集" description="创建后可在单元格中直接引用该数据集">
          <el-select
            v-model="form.dataset_id"
            placeholder="选择数据集（可选）"
            clearable
            filterable
            style="width: 100%"
            :loading="datasetsLoading"
          >
            <el-option v-for="ds in datasets" :key="ds.id" :label="`${ds.name} (${formatInteger(ds.rows)} 行)`" :value="ds.id" />
          </el-select>
        </SectionCard>

        <SectionCard title="绑定计算槽" description="绑定后单元格可将训练分发到所选资源执行">
          <div v-if="workersLoading" v-loading="true" class="workers-loading" />
          <template v-else-if="workerOptions.length > 0">
            <el-checkbox-group v-model="form.bound_workers" class="worker-grid">
              <el-checkbox
                v-for="w in workerOptions"
                :key="w.worker_id"
                :value="w.worker_id"
                border
                class="worker-checkbox"
              >
                <span class="worker-name mono">{{ w.worker_id }}</span>
                <el-tag size="small" :type="w.device === 'cpu' ? 'info' : 'success'">
                  {{ w.device === 'cpu' ? 'CPU' : (w.gpu_name || 'GPU') }}
                </el-tag>
                <el-tag v-if="w.state === 'busy' || w.current_job" size="small" type="warning">占用中</el-tag>
              </el-checkbox>
            </el-checkbox-group>
          </template>
          <EmptyState
            v-else
            title="暂无可用计算槽"
            description="可不绑定创建，稍后在会话中选择资源"
          />
        </SectionCard>

        <SectionCard title="高级配置" description="环境变量与依赖配置（JSON，可选）">
          <el-input
            v-model="form.metadata_json"
            type="textarea"
            :rows="4"
            placeholder='JSON 格式，如 {"env": {"BATCH_SIZE": "32"}}'
            class="mono"
          />
          <div v-if="metadataError" class="metadata-error">
            <el-icon><WarningFilled /></el-icon>
            {{ metadataError }}
          </div>
        </SectionCard>
      </div>

      <!-- 配置摘要 -->
      <div class="create-side">
        <SectionCard title="配置摘要" description="确认无误后提交">
          <div class="summary-list">
            <div class="summary-row">
              <span class="summary-label">会话名称</span>
              <span class="summary-value" :class="{ 'is-empty': !form.name }">{{ form.name || '未填写' }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">内核类型</span>
              <span class="summary-value">{{ KERNEL_OPTIONS.find((k) => k.value === form.kernel_type)?.label }}</span>
            </div>
            <div class="summary-row">
              <span class="summary-label">关联数据集</span>
              <span class="summary-value" :class="{ 'is-empty': !selectedDataset }">
                {{ selectedDataset ? selectedDataset.name : '未关联' }}
              </span>
            </div>
            <div class="summary-row">
              <span class="summary-label">绑定资源</span>
              <span class="summary-value" :class="{ 'is-empty': form.bound_workers.length === 0 }">
                {{ form.bound_workers.length > 0 ? `${form.bound_workers.length} 个计算槽` : '未绑定（会话内选择）' }}
              </span>
            </div>
            <div class="summary-row">
              <span class="summary-label">环境配置</span>
              <span class="summary-value" :class="{ 'is-empty': !hasMetadata }">
                {{ hasMetadata ? '自定义 JSON' : '默认' }}
              </span>
            </div>
          </div>
          <el-button
            type="primary"
            size="large"
            class="w-full mt-16"
            :loading="creating"
            @click="submit"
          >
            创建并打开
          </el-button>
          <el-button class="w-full mt-8" @click="router.push('/workbench/sessions')">取消</el-button>
        </SectionCard>

        <SectionCard title="说明">
          <ul class="help-list">
            <li>会话创建后默认包含一个代码单元格，可在工作台中继续添加。</li>
            <li>内核可在会话详情中随时启动或停止。</li>
            <li>计算槽被其他任务占用时仍可绑定，执行时排队。</li>
          </ul>
        </SectionCard>
      </div>
    </div>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * 新建会话：分组配置（基本信息 / 数据集 / 计算槽 / 高级）+ 实时配置摘要。
 * - 环境配置 JSON 前端校验，非法时阻止提交并提示；
 * - 摘要随表单实时更新，右侧确认后一键创建并跳转会话详情。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { formatInteger, tryParseJSON } from '@/utils/format'
import { Back, WarningFilled, Notebook, Tools } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

/** 内核类型选项（选择卡片） */
const KERNEL_OPTIONS = [
  { value: 'python', label: 'Python', desc: '适用于数据分析与模型训练', icon: Notebook },
  { value: 'bash', label: 'Bash', desc: '适用于脚本与命令行任务', icon: Tools },
] as const

const formRef = ref<FormInstance>()
const creating = ref(false)
const datasets = ref<any[]>([])
const datasetsLoading = ref(false)
const workerOptions = ref<any[]>([])
const workersLoading = ref(false)

const form = reactive({
  name: '',
  dataset_id: '',
  kernel_type: 'python',
  bound_workers: [] as string[],
  metadata_json: '{}',
})

const rules: FormRules = {
  name: [{ required: true, message: '请输入会话名称', trigger: 'blur' }],
  kernel_type: [{ required: true, message: '请选择内核类型', trigger: 'change' }],
}

/** 环境配置 JSON 校验错误信息 */
const metadataError = ref('')

/** 是否填写了自定义环境配置 */
const hasMetadata = computed(() => {
  const parsed = tryParseJSON(form.metadata_json)
  return parsed !== undefined && Object.keys(parsed).length > 0
})

/** 选中的数据集对象（摘要展示名称） */
const selectedDataset = computed(() => datasets.value.find((ds) => ds.id === form.dataset_id))

const fetchDatasets = async () => {
  datasetsLoading.value = true
  try {
    const res: any = await api.get('/datasets', { limit: 200 })
    datasets.value = res.datasets || []
  } catch (err: any) {
    appStore.warning('数据集加载失败', err?.message)
  } finally {
    datasetsLoading.value = false
  }
}

const fetchWorkers = async () => {
  workersLoading.value = true
  try {
    const res: any = await api.get('/clusters/workers/summary')
    workerOptions.value = res.details || []
  } catch (err: any) {
    appStore.warning('计算槽加载失败', err?.message)
  } finally {
    workersLoading.value = false
  }
}

/** 提交创建：校验名称与 JSON，成功后跳转会话详情 */
const submit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  // 环境配置 JSON 校验（空串按空对象处理）
  let metadata: Record<string, any> = {}
  if (form.metadata_json.trim()) {
    const parsed = tryParseJSON(form.metadata_json)
    if (parsed === undefined) {
      metadataError.value = '环境配置必须是合法的 JSON 对象'
      return
    }
    metadata = parsed
  }
  metadataError.value = ''

  creating.value = true
  try {
    const res: any = await api.post('/workbench/sessions', {
      name: form.name,
      dataset_id: form.dataset_id || null,
      kernel_type: form.kernel_type,
      bound_workers: form.bound_workers,
      metadata,
    })
    appStore.success('会话创建成功', form.name)
    router.push(`/workbench/session/${res.session_id}`)
  } catch (err: any) {
    appStore.error('创建失败', err?.message)
  } finally {
    creating.value = false
  }
}

onMounted(() => {
  fetchDatasets()
  fetchWorkers()
})
</script>

<style scoped>
.create-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1200px) {
  .create-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.create-main,
.create-side {
  min-width: 0;
}

/* 内核选择卡片 */
.kernel-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: 100%;
}

@media (max-width: 768px) {
  .kernel-options {
    grid-template-columns: minmax(0, 1fr);
  }
}

.kernel-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px;
  border: 1px solid var(--mtp-ink-200);
  border-radius: var(--mtp-radius-md);
  background: var(--mtp-bg-card);
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}

.kernel-option:hover {
  border-color: var(--mtp-brand-500);
}

.kernel-option.is-active {
  border-color: var(--mtp-brand-600);
  background: var(--mtp-brand-50);
  box-shadow: 0 0 0 2px rgba(48, 86, 211, 0.15);
}

.kernel-option__name {
  font-weight: 600;
  color: var(--mtp-ink-900);
  font-size: 14px;
}

.kernel-option__desc {
  font-size: 12px;
  color: var(--mtp-ink-400);
}

.workers-loading {
  height: 48px;
  width: 100%;
}

.worker-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
  width: 100%;
}

.worker-checkbox {
  margin: 0;
  padding: 10px 14px;
  height: auto;
}

.worker-checkbox :deep(.el-checkbox__label) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.worker-name {
  font-size: 12px;
}

.metadata-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: var(--mtp-radius-sm);
  background: var(--el-color-danger-light-9);
  color: var(--mtp-danger);
  font-size: 13px;
}

/* 配置摘要 */
.summary-list {
  display: flex;
  flex-direction: column;
}

.summary-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--mtp-ink-100);
  font-size: 13px;
}

.summary-row:last-child {
  border-bottom: none;
}

.summary-label {
  color: var(--mtp-ink-400);
  flex-shrink: 0;
}

.summary-value {
  color: var(--mtp-ink-900);
  font-weight: 500;
  text-align: right;
  word-break: break-all;
}

.summary-value.is-empty {
  color: var(--mtp-ink-300);
  font-weight: 400;
}

.help-list {
  padding-left: 18px;
  color: var(--mtp-ink-500);
  font-size: 13px;
  line-height: 2;
}
</style>
