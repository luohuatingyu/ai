import { describe, it, expect } from 'vitest'
import { JOB_STATUS, DATASET_SOURCE, TASK_TYPE, resolveStatus } from '../status'

describe('resolveStatus', () => {
  it('已登记状态返回映射定义', () => {
    expect(resolveStatus(JOB_STATUS, 'running')).toEqual({ label: '运行中', type: 'primary' })
    expect(resolveStatus(JOB_STATUS, 'completed')).toEqual({ label: '已完成', type: 'success' })
    expect(resolveStatus(JOB_STATUS, 'failed')).toEqual({ label: '失败', type: 'danger' })
  })

  it('未登记状态原样展示并标记 info（后端新增枚举不崩溃）', () => {
    expect(resolveStatus(JOB_STATUS, 'future_state')).toEqual({ label: 'future_state', type: 'info' })
  })

  it('空值返回占位符', () => {
    expect(resolveStatus(JOB_STATUS, '')).toEqual({ label: '-', type: 'info' })
    expect(resolveStatus(JOB_STATUS, null)).toEqual({ label: '-', type: 'info' })
    expect(resolveStatus(JOB_STATUS, undefined)).toEqual({ label: '-', type: 'info' })
  })
})

describe('状态映射表完整性', () => {
  it('训练任务状态覆盖六种已知枚举', () => {
    for (const status of ['queued', 'running', 'paused', 'completed', 'failed', 'cancelled']) {
      expect(JOB_STATUS[status], `缺少状态 ${status}`).toBeDefined()
    }
  })

  it('数据集来源覆盖三种已知枚举', () => {
    for (const source of ['local', 'huggingface', 'oss']) {
      expect(DATASET_SOURCE[source], `缺少来源 ${source}`).toBeDefined()
    }
  })

  it('任务类型覆盖平台支持的四类基础任务', () => {
    for (const task of ['classification', 'regression', 'clustering', 'time_series_forecasting']) {
      expect(TASK_TYPE[task], `缺少任务类型 ${task}`).toBeDefined()
    }
  })
})
