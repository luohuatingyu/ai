import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/Dashboard.vue'),
    meta: { title: '仪表盘' },
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
        meta: { title: '数据集列表' },
      },
      {
        path: 'import',
        name: 'DatasetImport',
        component: () => import('@/views/datasets/DatasetImport.vue'),
        meta: { title: '导入数据集' },
      },
      {
        path: 'huggingface',
        name: 'DatasetHuggingFace',
        component: () => import('@/views/datasets/DatasetHuggingFace.vue'),
        meta: { title: 'HuggingFace 数据集' },
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
        meta: { title: '会话管理' },
      },
      {
        path: 'new',
        name: 'WorkbenchNew',
        component: () => import('@/views/workbench/NewSession.vue'),
        meta: { title: '新建会话' },
      },
      {
        path: 'session/:id',
        name: 'WorkbenchSession',
        component: () => import('@/views/workbench/SessionDetail.vue'),
        meta: { title: '会话详情' },
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
        meta: { title: '模板库' },
      },
      {
        path: 'my',
        name: 'MyTemplates',
        component: () => import('@/views/templates/MyTemplates.vue'),
        meta: { title: '我的模板' },
      },
      {
        path: 'create',
        name: 'TemplateCreate',
        component: () => import('@/views/templates/TemplateCreate.vue'),
        meta: { title: '创建模板' },
      },
      {
        path: 'detail/:id',
        name: 'TemplateDetail',
        component: () => import('@/views/templates/TemplateDetail.vue'),
        meta: { title: '模板详情' },
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
        meta: { title: '训练任务列表' },
      },
      {
        path: 'create',
        name: 'TrainingCreate',
        component: () => import('@/views/training/JobCreate.vue'),
        meta: { title: '创建训练任务' },
      },
      {
        path: 'detail/:id',
        name: 'TrainingDetail',
        component: () => import('@/views/training/JobDetail.vue'),
        meta: { title: '任务详情' },
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
        meta: { title: '模型注册表' },
      },
      {
        path: 'deployments',
        name: 'ModelDeployments',
        component: () => import('@/views/models/Deployments.vue'),
        meta: { title: '部署管理' },
      },
      {
        path: 'detail/:id',
        name: 'ModelDetail',
        component: () => import('@/views/models/ModelDetail.vue'),
        meta: { title: '模型详情' },
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
        meta: { title: 'Pipeline 列表' },
      },
      {
        path: 'create',
        name: 'PipelineCreate',
        component: () => import('@/views/pipelines/PipelineCreate.vue'),
        meta: { title: '创建 Pipeline' },
      },
      {
        path: 'runs',
        name: 'PipelineRuns',
        component: () => import('@/views/pipelines/PipelineRuns.vue'),
        meta: { title: '执行历史' },
      },
      {
        path: 'detail/:id',
        name: 'PipelineDetail',
        component: () => import('@/views/pipelines/PipelineDetail.vue'),
        meta: { title: 'Pipeline 详情' },
      },
      {
        path: 'run/:pipelineId/:runId',
        name: 'PipelineRunDetail',
        component: () => import('@/views/pipelines/PipelineRunDetail.vue'),
        meta: { title: '执行详情' },
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
        meta: { title: 'Worker 管理' },
      },
      {
        path: 'remote',
        name: 'ClusterRemote',
        component: () => import('@/views/clusters/RemoteServers.vue'),
        meta: { title: '远程服务器' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

// 路由守卫
router.beforeEach((to, from, next) => {
  document.title = `${to.meta.title || 'MTP'} - 模型训练平台`
  next()
})

export default router