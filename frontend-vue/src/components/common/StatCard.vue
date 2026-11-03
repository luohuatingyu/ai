<template>
  <component
    :is="clickable ? 'button' : 'div'"
    class="stat-card"
    :class="{ 'stat-card--clickable': clickable }"
    :type="clickable ? 'button' : undefined"
    @click="clickable && emit('click')"
  >
    <div class="stat-card__top">
      <span class="stat-card__label">{{ label }}</span>
      <span v-if="icon" class="stat-card__icon" :class="`tone-${tone}`">
        <el-icon><component :is="icon" /></el-icon>
      </span>
    </div>
    <div class="stat-card__value">{{ displayValue }}</div>
    <div v-if="hint" class="stat-card__hint">{{ hint }}</div>
    <div v-if="$slots.footer" class="stat-card__footer">
      <slot name="footer" />
    </div>
  </component>
</template>

<script setup lang="ts">
/**
 * 统计卡片：仪表盘与各列表页顶部的关键指标。
 * - clickable 时渲染为 button（键盘可达，回车触发 click）；
 * - tone 控制图标底色语义（蓝=任务、绿=成功、橙=资源、红=异常…）。
 */
import { computed, type Component } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 指标名称 */
    label: string
    /** 指标值（数字或文本） */
    value: number | string
    /** 可选图标（Element Plus 图标组件） */
    icon?: Component
    /** 图标色调 */
    tone?: 'blue' | 'green' | 'orange' | 'red' | 'purple' | 'cyan' | 'gray'
    /** 补充说明 */
    hint?: string
    /** 是否可点击跳转 */
    clickable?: boolean
  }>(),
  {
    icon: undefined,
    tone: 'blue',
    hint: '',
    clickable: false,
  }
)

const emit = defineEmits<{ click: [] }>()

/** 数值展示：computed 保持响应（props.value 更新后文本同步刷新） */
const displayValue = computed(() => props.value)
</script>
