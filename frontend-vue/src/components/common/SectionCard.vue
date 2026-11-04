<template>
  <section class="section-card">
    <header v-if="title || $slots.extra" class="section-card__header">
      <div>
        <div class="section-card__title">
          {{ title }}
          <el-tooltip v-if="tip" :content="tip" placement="top">
            <el-icon class="section-card__tip"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
        <div v-if="description" class="section-card__desc">{{ description }}</div>
      </div>
      <div class="section-card__extra">
        <slot name="extra" />
      </div>
    </header>
    <div class="section-card__body" :class="{ 'is-flush': flush }">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 区块卡片：内容分区容器，带统一样式的标题栏。
 * - flush=true 时内容区无内边距（用于放表格，让表格通栏铺满）；
 * - tip 用于在标题旁补充说明（鼠标悬停展示）。
 */
import { QuestionFilled } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    /** 区块标题（纯内容包裹场景可省略标题栏） */
    title?: string
    /** 标题补充说明（副文案） */
    description?: string
    /** 标题旁提示（悬停展示） */
    tip?: string
    /** 内容区是否通栏（无内边距，用于表格） */
    flush?: boolean
  }>(),
  {
    description: '',
    tip: '',
    flush: false,
  }
)
</script>

<style scoped>
.section-card__extra {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.section-card__tip {
  color: var(--mtp-ink-400);
  cursor: help;
  font-size: 14px;
}

.section-card__body.is-flush {
  padding: 0;
}
</style>
