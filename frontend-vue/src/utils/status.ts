/**
 * 统一状态映射：把后端状态值转换为中文标签 + Element Plus tag 类型。
 * 各页面共用，避免同一种状态在不同页面显示不一致。
 * 后续阶段接入工作台 / 部署 / 集群页面时按实际后端枚举扩展。
 */

/** Element Plus tag 的语义类型 */
export type TagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

/** 一条状态的展示定义 */
export interface StatusMeta {
  label: string
  type: TagType
}

/** 训练任务状态（backend job status） */
export const JOB_STATUS: Record<string, StatusMeta> = {
  queued: { label: '排队中', type: 'info' },
  running: { label: '运行中', type: 'primary' },
  paused: { label: '已暂停', type: 'warning' },
  completed: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
}

/** 交互式工作台会话状态 */
export const SESSION_STATUS: Record<string, StatusMeta> = {
  starting: { label: '启动中', type: 'info' },
  running: { label: '运行中', type: 'success' },
  idle: { label: '空闲', type: 'warning' },
  busy: { label: '忙碌', type: 'primary' },
  stopped: { label: '已停止', type: 'info' },
  error: { label: '错误', type: 'danger' },
}

/** 模型状态（注册表） */
export const MODEL_STATUS: Record<string, StatusMeta> = {
  ready: { label: '可用', type: 'success' },
  archived: { label: '已归档', type: 'info' },
}

/** 部署实例状态 */
export const DEPLOY_STATUS: Record<string, StatusMeta> = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '运行中', type: 'success' },
  stopped: { label: '已停止', type: 'warning' },
  failed: { label: '失败', type: 'danger' },
}

/** Worker 状态（active=false 时由页面单独显示离线） */
export const WORKER_STATE: Record<string, StatusMeta> = {
  idle: { label: '空闲', type: 'success' },
  reserved: { label: '已预约', type: 'warning' },
  busy: { label: '忙碌中', type: 'primary' },
  disabled: { label: '已禁用', type: 'info' },
}

/** 远程服务器生命周期状态 */
export const SERVER_STATUS: Record<string, StatusMeta> = {
  not_deployed: { label: '未部署', type: 'info' },
  provisioning: { label: '装机中', type: 'warning' },
  configuring: { label: '下发配置', type: 'warning' },
  deploying: { label: '部署中', type: 'warning' },
  starting: { label: '启动中', type: 'warning' },
  stopping: { label: '停止中', type: 'warning' },
  restarting: { label: '重启中', type: 'warning' },
  online: { label: '在线', type: 'success' },
  stopped: { label: '已停止', type: 'info' },
  error: { label: '异常', type: 'danger' },
}

/** 数据集来源 */
export const DATASET_SOURCE: Record<string, StatusMeta> = {
  local: { label: '本地上传', type: 'success' },
  huggingface: { label: 'HuggingFace', type: 'primary' },
  oss: { label: '对象存储', type: 'warning' },
}

/** 训练任务类型 */
export const TASK_TYPE: Record<string, StatusMeta> = {
  classification: { label: '分类', type: 'primary' },
  regression: { label: '回归', type: 'success' },
  clustering: { label: '聚类', type: 'warning' },
  time_series_forecasting: { label: '时间序列', type: 'info' },
  time_series: { label: '时间序列', type: 'info' },
  llm_pretraining: { label: 'LLM 预训练', type: 'danger' },
  llm_sft: { label: 'LLM 微调', type: 'danger' },
  llm_dpo: { label: 'LLM DPO', type: 'danger' },
  llm_ppo: { label: 'LLM PPO', type: 'danger' },
}

/**
 * 按映射键解析状态；未登记的状态原样展示并标记为 info，
 * 保证后端新增枚举时前端不崩溃、不误标。
 */
export function resolveStatus(
  map: Record<string, StatusMeta>,
  status: string | null | undefined
): StatusMeta {
  if (!status) return { label: '-', type: 'info' }
  return map[status] || { label: status, type: 'info' }
}
