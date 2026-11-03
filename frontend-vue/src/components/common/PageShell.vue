<template>
  <div class="page-view" v-loading="loading">
    <!-- 面包屑：模块 / 当前页 -->
    <el-breadcrumb v-if="showBreadcrumb" class="page-breadcrumb" separator="/">
      <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
      <el-breadcrumb-item v-if="module">{{ module }}</el-breadcrumb-item>
      <el-breadcrumb-item>{{ title }}</el-breadcrumb-item>
    </el-breadcrumb>

    <div class="page-header">
      <div class="page-header__main">
        <h1 class="page-title">{{ title }}</h1>
        <p v-if="description || $slots.description" class="page-desc">
          <slot name="description">{{ description }}</slot>
        </p>
      </div>
      <div v-if="$slots.actions" class="page-actions">
        <slot name="actions" />
      </div>
    </div>

    <slot />
  </div>
</template>

<script setup lang="ts">
/**
 * 页面壳：统一"面包屑 + 标题 + 描述 + 主操作区 + 内容"结构。
 * 所有业务页面用本组件组织头部，保证 24 个页面的信息层级一致。
 */
withDefaults(
  defineProps<{
    /** 页面标题 */
    title: string
    /** 页面职责描述（副标题） */
    description?: string
    /** 所属模块（面包屑第二级，通常取 route.meta.module） */
    module?: string
    /** 是否展示面包屑（个别全屏页面可关闭） */
    showBreadcrumb?: boolean
    /** 内容加载遮罩 */
    loading?: boolean
  }>(),
  {
    description: '',
    module: '',
    showBreadcrumb: true,
    loading: false,
  }
)
</script>
