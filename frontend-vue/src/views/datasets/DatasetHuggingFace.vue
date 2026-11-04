<template>
  <PageShell title="HuggingFace 数据集" module="数据集管理" description="从 HuggingFace Hub 导入公开数据集，支持引用或立即下载">
    <template #actions>
      <el-button @click="router.push('/datasets/list')">
        <el-icon class="gap-8"><Back /></el-icon>
        返回数据集列表
      </el-button>
    </template>

    <div class="hf-grid">
      <!-- 导入配置 -->
      <div class="hf-main">
        <!-- 快捷填充 -->
        <SectionCard title="常用数据集" tip="点击即可填充到下方表单" description="平台内置的快速入口">
          <div class="preset-chips">
            <button
              v-for="preset in PRESET_DATASETS"
              :key="preset.repo_id"
              type="button"
              class="preset-chip"
              @click="applyPreset(preset)"
            >
              {{ preset.repo_id }}
            </button>
          </div>
        </SectionCard>

        <SectionCard title="导入配置">
          <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
            <el-divider content-position="left">仓库信息</el-divider>
            <el-form-item label="仓库 ID" prop="repo_id">
              <el-input v-model="form.repo_id" placeholder="如 imdb、squad、glue" clearable />
              <div class="tip-text">仅支持公开仓库，格式为 owner/name 或单名数据集</div>
            </el-form-item>
            <el-form-item label="版本" prop="revision">
              <el-input v-model="form.revision" placeholder="main（默认）" />
            </el-form-item>
            <el-form-item label="子集">
              <el-input v-model="form.subset" placeholder="可选，如 en、zh" />
            </el-form-item>

            <el-divider content-position="left">数据切分与存储</el-divider>
            <el-form-item label="Split" prop="split">
              <el-select v-model="form.split" style="width: 100%">
                <el-option label="train（训练集）" value="train" />
                <el-option label="test（测试集）" value="test" />
                <el-option label="validation（验证集）" value="validation" />
              </el-select>
            </el-form-item>
            <el-form-item label="存储模式" prop="storage_mode">
              <el-radio-group v-model="form.storage_mode">
                <el-radio value="remote">引用（首次使用时拉取）</el-radio>
                <el-radio value="local">立即下载到本地</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-divider content-position="left">描述信息</el-divider>
            <el-form-item label="描述">
              <el-input v-model="form.description" type="textarea" :rows="2" placeholder="数据集描述（可选）" maxlength="300" show-word-limit />
            </el-form-item>
            <el-form-item label="标签">
              <el-select
                v-model="form.tags"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="输入标签后回车创建"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" size="large" :loading="submitting" @click="submit">
                <el-icon class="gap-8"><Download /></el-icon>
                导入数据集
              </el-button>
            </el-form-item>
          </el-form>
        </SectionCard>
      </div>

      <!-- 说明与结果 -->
      <div class="hf-side">
        <SectionCard title="存储模式说明">
          <ul class="help-list">
            <li><b>引用模式</b>仅保存仓库引用，训练或预览首次使用时自动拉取，不占用当前空间。</li>
            <li><b>立即下载</b>会在导入时下载所选 split 到本地存储。</li>
            <li>导入完成后可在数据集列表执行「下载到本地」补拉数据。</li>
            <li>平台仅访问 HuggingFace 公开仓库，无需配置访问令牌。</li>
          </ul>
        </SectionCard>

        <!-- 导入结果面板 -->
        <SectionCard v-if="lastResult" title="导入结果">
          <el-result
            icon="success"
            title="导入成功"
            :sub-title="`数据集 ID：${lastResult.id}`"
            style="padding: 24px 0"
          >
            <template #extra>
              <div class="result-actions">
                <el-tag v-if="lastResult.materialized === true" type="success" effect="light">数据已拉取到本地</el-tag>
                <el-tag v-else type="info" effect="light">数据将在首次使用时自动拉取</el-tag>
                <el-button type="primary" @click="router.push('/datasets/list')">查看数据集列表</el-button>
              </div>
            </template>
          </el-result>
        </SectionCard>
      </div>
    </div>
  </PageShell>
</template>

<script setup lang="ts">
/**
 * HuggingFace 数据集导入：仓库配置表单 + 存储模式说明 + 导入结果面板。
 * - 表单校验失败时定位到具体字段，输入保留；
 * - 引用模式导入成功后立即尝试物化，物化失败不影响导入结果（降级为提示）；
 * - 快捷入口仅填充表单，不直接发起请求。
 */
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { api } from '@/api'
import { useAppStore } from '@/stores/app'
import PageShell from '@/components/common/PageShell.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { Back, Download } from '@element-plus/icons-vue'

const router = useRouter()
const appStore = useAppStore()

/** 常用公开数据集快捷入口（仅填充表单） */
const PRESET_DATASETS = [
  { repo_id: 'imdb', split: 'train' },
  { repo_id: 'squad', split: 'train' },
  { repo_id: 'glue', split: 'train' },
  { repo_id: 'mnist', split: 'train' },
] as const

const formRef = ref<FormInstance>()
const submitting = ref(false)
const form = reactive({
  repo_id: '',
  revision: 'main',
  subset: '',
  split: 'train',
  storage_mode: 'remote',
  description: '',
  tags: [] as string[],
})

/** 最近一次导入结果（右侧结果面板展示） */
const lastResult = ref<{ id: string; materialized: boolean } | null>(null)

const rules: FormRules = {
  repo_id: [
    { required: true, message: '请输入 HuggingFace 仓库 ID', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]*(\/[A-Za-z0-9][A-Za-z0-9._-]*)?$/, message: '仓库 ID 格式无效', trigger: 'blur' },
  ],
  revision: [{ required: true, message: '请输入版本', trigger: 'blur' }],
  split: [{ required: true, message: '请选择 split', trigger: 'change' }],
}

/** 快捷入口：填充仓库 ID 与默认 split */
const applyPreset = (preset: { repo_id: string; split: string }) => {
  form.repo_id = preset.repo_id
  form.split = preset.split
}

const submit = async () => {
  // 校验失败：Element Plus 定位到具体字段，直接返回
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    const payload: Record<string, any> = {
      repo_id: form.repo_id,
      revision: form.revision,
      split: form.split,
      storage_mode: form.storage_mode,
      description: form.description,
      tags: form.tags,
    }
    if (form.subset) payload.subset = form.subset
    const res: any = await api.post('/datasets/hub', payload)
    appStore.success('HuggingFace 数据集导入成功', `数据集 ID：${res.id || ''}`)

    // 引用模式导入后立即尝试物化，失败不影响导入结果
    let materialized = false
    if (form.storage_mode === 'remote' && res.id) {
      try {
        await api.post(`/datasets/${res.id}/materialize`)
        materialized = true
        appStore.success('数据已拉取到本地')
      } catch {
        appStore.info('数据将在首次使用时自动拉取')
      }
    }
    lastResult.value = { id: res.id || '-', materialized }
  } catch (err: any) {
    appStore.error('导入失败', err?.message)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.hf-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 16px;
  align-items: start;
}

@media (max-width: 1200px) {
  .hf-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.hf-main,
.hf-side {
  min-width: 0;
}

.hf-side {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.preset-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.preset-chip {
  cursor: pointer;
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--mtp-ink-200);
  background: var(--mtp-bg-card);
  color: var(--mtp-ink-700);
  transition: all 0.15s;
}

.preset-chip:hover {
  color: var(--mtp-brand-600);
  border-color: var(--mtp-brand-500);
  background: var(--mtp-brand-50);
}

.tip-text {
  font-size: 12px;
  color: var(--mtp-ink-400);
  line-height: 1.6;
  margin-top: 4px;
}

.help-list {
  padding-left: 18px;
  color: var(--mtp-ink-500);
  font-size: 13px;
  line-height: 2;
}

.help-list b {
  color: var(--mtp-ink-700);
}

.result-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}
</style>
