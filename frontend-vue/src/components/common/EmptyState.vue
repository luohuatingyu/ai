<template>
  <div class="empty-state" :class="{ 'empty-state--error': type === 'error' }">
    <el-icon class="empty-state-icon" :size="44">
      <component :is="resolvedIcon" />
    </el-icon>
    <div class="empty-state__title">{{ title }}</div>
    <div v-if="description" class="empty-state__desc">{{ description }}</div>
    <div class="empty-state__actions">
      <el-button v-if="retryable" type="primary" plain size="small" @click="emit('retry')">
        <el-icon><Refresh /></el-icon>
        重试
      </el-button>
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 空态 / 异常态：统一"图标 + 标题 + 描述 + 操作"结构。
 * - type=empty：数据为空（如暂无数据集）；
 * - type=error：接口失败，retryable 时提供重试按钮（retry 事件由页面处理）。
 */
import { computed, type Component } from 'vue'
import { Box, WarningFilled, Refresh } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    /** 展示类型 */
    type?: 'empty' | 'error'
    /** 主文案 */
    title?: string
    /** 补充说明 */
    description?: string
    /** 是否显示重试按钮（发出 retry 事件） */
    retryable?: boolean
    /** 自定义图标 */
    icon?: Component
  }>(),
  {
    type: 'empty',
    title: '',
    description: '',
    retryable: false,
    icon: undefined,
  }
)

const emit = defineEmits<{ retry: [] }>()

/** 默认图标：空态用盒子，异常用警告 */
const resolvedIcon = computed(() => props.icon || (props.type === 'error' ? WarningFilled : Box))
</script>

<style scoped>
.empty-state--error .empty-state-icon {
  color: var(--mtp-danger);
}

.empty-state__title {
  font-size: 15px;
  font-weight: 500;
  color: var(--mtp-ink-700);
  margin-bottom: 4px;
}

.empty-state__desc {
  font-size: 13px;
  color: var(--mtp-ink-400);
  max-width: 420px;
  margin: 0 auto;
}

.empty-state__actions {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;
}
</style>
