<template>
  <el-tag
    :type="meta.type"
    :size="size"
    :effect="effect"
    disable-transitions
    class="status-tag"
    :class="{ 'is-active': isActive }"
  >
    <span class="status-tag__dot" aria-hidden="true" />
    {{ meta.label }}
  </el-tag>
</template>

<script setup lang="ts">
/**
 * 统一状态标签：按映射把后端状态转为中文标签 + 语义色。
 * 未登记状态原样展示（info 色），避免后端新增枚举时前端崩溃。
 * - 圆点视觉编码：所有状态前置语义色圆点；
 * - 进行中状态（运行/启动/部署等过渡态）圆点带呼吸动画，扫一眼即可区分
 *   "正在发生"与"已定格"的状态。
 */
import { computed } from 'vue'
import { resolveStatus, type StatusMeta } from '@/utils/status'

/** 视为"进行中"的状态集合（跨业务域的过渡态枚举） */
const ACTIVE_STATES = new Set([
  'running', 'starting', 'provisioning', 'configuring', 'deploying',
  'stopping', 'restarting', 'pending',
])

const props = withDefaults(
  defineProps<{
    /** 状态原始值 */
    status: string | null | undefined
    /** 使用的状态映射表 */
    map: Record<string, StatusMeta>
    /** 尺寸 */
    size?: 'small' | 'default' | 'large'
    /** 填充样式 */
    effect?: 'dark' | 'light' | 'plain'
  }>(),
  {
    size: 'small',
    effect: 'light',
  }
)

/** 解析后的状态定义（标签文案 + tag 类型） */
const meta = computed(() => resolveStatus(props.map, props.status))

/** 是否为进行中状态（圆点加呼吸动画） */
const isActive = computed(() => ACTIVE_STATES.has(props.status || ''))
</script>
