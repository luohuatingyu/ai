<template>
  <el-tag :type="meta.type" :size="size" :effect="effect" disable-transitions>
    {{ meta.label }}
  </el-tag>
</template>

<script setup lang="ts">
/**
 * 统一状态标签：按映射把后端状态转为中文标签 + 语义色。
 * 未登记状态原样展示（info 色），避免后端新增枚举时前端崩溃。
 */
import { computed } from 'vue'
import { resolveStatus, type StatusMeta } from '@/utils/status'

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
</script>
