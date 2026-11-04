import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

/**
 * 路由表
 * meta 约定：
 * - title：页面标题（document.title 与页面头部共用）
 * - module：所属业务模块（面包屑第一级）
 * - nav：侧栏菜单高亮定位（详情页指向所属列表页）
 * - desc：页面职责描述（页面头部副标题）
 */
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '仪表盘', module: '总览', nav: '/dashboard', desc: '平台运行总览：训练进展、模型资产、计算资源与常用入口' },
  },

  // 数据集管理
  {
    path: '/datasets',
    redirect: '/datasets/list',
    children: [
      {
        path: 'list',
        name: 'DatasetList',
        component: () => import('@/views/datasets/DatasetList.vue'),
        meta: { title: '数据集列表', module: '数据集管理', nav: '/datasets/list', desc: '统一浏览、检索与管理平台数据集，支持预览、统计与预处理' },
      },
      {
        path: 'import',
        name: 'DatasetImport',
        component: () => import('@/views/datasets/DatasetImport.vue'),
        meta: { title: '导入数据集', module: '数据集管理', nav: '/datasets/list', desc: '上传本地 CSV / 压缩包并完成解析配置' },
      },
      {
        path: 'huggingface',
        name: 'DatasetHuggingFace',
        component: () => import('@/views/datasets/DatasetHuggingFace.vue'),
        meta: { title: 'HuggingFace 数据集', module: '数据集管理', nav: '/datasets/list', desc: '检索 HuggingFace Hub 数据集并导入平台' },
      },
    ],
  },

  // 工作台
  {
    path: '/workbench',
    redirect: '/workbench/sessions',
    children: [
      {
        path: 'sessions',
        name: 'WorkbenchSessions',
        component: () => import('@/views/workbench/SessionList.vue'),
        meta: { title: '会话管理', module: '交互式工作台', nav: '/workbench/sessions', desc: '管理 Notebook 会话：状态、资源与最近活动' },
      },
      {
        path: 'new',
        name: 'WorkbenchNew',
        component: () => import('@/views/workbench/NewSession.vue'),
        meta: { title: '新建会话', module: '交互式工作台', nav: '/workbench/sessions', desc: '选择运行环境与资源规格，创建新的 Notebook 会话' },
      },
      {
        path: 'session/:id',
        name: 'WorkbenchSession',
        component: () => import('@/views/workbench/SessionDetail.vue'),
        meta: { title: '会话详情', module: '交互式工作台', nav: '/workbench/sessions', desc: '在线 Notebook 编辑与单元格执行' },
      },
    ],
  },

  // 模板库
  {
    path: '/templates',
    redirect: '/templates/library',
    children: [
      {
        path: 'library',
        name: 'TemplateLibrary',
        component: () => import('@/views/templates/TemplateLibrary.vue'),
        meta: { title: '模板库', module: '脚本模板库', nav: '/templates/library', desc: '按任务类型浏览内置脚本模板，一键用于训练任务' },
      },
      {
        path: 'my',
        name: 'MyTemplates',
        component: () => import('@/views/templates/MyTemplates.vue'),
        meta: { title: '我的模板', module: '脚本模板库', nav: '/templates/my', desc: '管理自建脚本模板：编辑、使用与删除' },
      },
      {
        path: 'create',
        name: 'TemplateCreate',
        component: () => import('@/views/templates/TemplateCreate.vue'),
        meta: { title: '创建模板', module: '脚本模板库', nav: '/templates/my', desc: '定义脚本模板的基础信息、参数与默认配置' },
      },
      {
        path: 'detail/:id',
        name: 'TemplateDetail',
        component: () => import('@/views/templates/TemplateDetail.vue'),
        meta: { title: '模板详情', module: '脚本模板库', nav: '/templates/library', desc: '查看模板脚本、参数说明与使用入口' },
      },
    ],
  },

  // 训练任务
  {
    path: '/training',
    redirect: '/training/jobs',
    children: [
      {
        path: 'jobs',
        name: 'TrainingJobs',
        component: () => import('@/views/training/JobList.vue'),
        meta: { title: '任务列表', module: '训练任务', nav: '/training/jobs', desc: '跟踪全部训练任务的状态、进度与结果' },
      },
      {
        path: 'create',
        name: 'TrainingCreate',
        component: () => import('@/views/training/JobCreate.vue'),
        meta: { title: '创建训练任务', module: '训练任务', nav: '/training/jobs', desc: '选择数据集、任务类型、算法与资源后提交训练' },
      },
      {
        path: 'detail/:id',
        name: 'TrainingDetail',
        component: () => import('@/views/training/JobDetail.vue'),
        meta: { title: '任务详情', module: '训练任务', nav: '/training/jobs', desc: '查看训练配置、实时指标、日志与产物' },
      },
    ],
  },

  // 模型注册表
  {
    path: '/models',
    redirect: '/models/registry',
    children: [
      {
        path: 'registry',
        name: 'ModelRegistry',
        component: () => import('@/views/models/ModelRegistry.vue'),
        meta: { title: '模型注册表', module: '模型注册表', nav: '/models/registry', desc: '平台模型资产清单：指标、版本与来源任务' },
      },
      {
        path: 'deployments',
        name: 'ModelDeployments',
        component: () => import('@/views/models/Deployments.vue'),
        meta: { title: '部署管理', module: '模型注册表', nav: '/models/deployments', desc: '管理模型在线部署实例与访问地址' },
      },
      {
        path: 'detail/:id',
        name: 'ModelDetail',
        component: () => import('@/views/models/ModelDetail.vue'),
        meta: { title: '模型详情', module: '模型注册表', nav: '/models/registry', desc: '查看模型指标、配置与产物，执行预测或部署' },
      },
    ],
  },

  // Pipeline 编排
  {
    path: '/pipelines',
    redirect: '/pipelines/list',
    children: [
      {
        path: 'list',
        name: 'PipelineList',
        component: () => import('@/views/pipelines/PipelineList.vue'),
        meta: { title: 'Pipeline 列表', module: 'Pipeline 编排', nav: '/pipelines/list', desc: '管理多步骤训练流水线：编辑、执行与调度' },
      },
      {
        path: 'create',
        name: 'PipelineCreate',
        component: () => import('@/views/pipelines/PipelineCreate.vue'),
        meta: { title: '创建 Pipeline', module: 'Pipeline 编排', nav: '/pipelines/list', desc: '以可视化画布编排步骤依赖与参数' },
      },
      {
        path: 'runs',
        name: 'PipelineRuns',
        component: () => import('@/views/pipelines/PipelineRuns.vue'),
        meta: { title: '执行历史', module: 'Pipeline 编排', nav: '/pipelines/runs', desc: '回溯 Pipeline 执行记录、耗时与结果' },
      },
      {
        path: 'detail/:id',
        name: 'PipelineDetail',
        component: () => import('@/views/pipelines/PipelineDetail.vue'),
        meta: { title: 'Pipeline 详情', module: 'Pipeline 编排', nav: '/pipelines/list', desc: '查看流程结构、步骤配置与执行入口' },
      },
      {
        path: 'run/:pipelineId/:runId',
        name: 'PipelineRunDetail',
        component: () => import('@/views/pipelines/PipelineRunDetail.vue'),
        meta: { title: '执行详情', module: 'Pipeline 编排', nav: '/pipelines/runs', desc: '跟踪一次执行中各步骤的状态与日志' },
      },
    ],
  },

  // 计算集群
  {
    path: '/clusters',
    redirect: '/clusters/workers',
    children: [
      {
        path: 'workers',
        name: 'ClusterWorkers',
        component: () => import('@/views/clusters/WorkerList.vue'),
        meta: { title: 'Worker 管理', module: '计算集群', nav: '/clusters/workers', desc: '监控训练节点心跳、资源占用与任务分布' },
      },
      {
        path: 'remote',
        name: 'ClusterRemote',
        component: () => import('@/views/clusters/RemoteServers.vue'),
        meta: { title: '远程服务器', module: '计算集群', nav: '/clusters/remote', desc: '管理远程训练服务器、SSH 凭证与远程操作' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

// 路由守卫：同步页面标题
router.beforeEach((to, _from, next) => {
  document.title = `${to.meta.title || 'MTP'} - 模型训练平台`
  next()
})

export default router
