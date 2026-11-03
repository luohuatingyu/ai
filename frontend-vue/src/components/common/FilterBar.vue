<template>
  <div class="filter-bar">
    <div class="filter-bar__main">
      <slot />
    </div>
    <div v-if="$slots.actions" class="filter-bar__side">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 筛选工具栏容器：统一筛选区的高度、留白与换行行为。
 * 左侧放筛选控件（搜索、下拉、日期等），右侧放次要操作（导出、刷新等）。
 */
</script>
