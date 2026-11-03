import dayjs from 'dayjs'

/**
 * 通用格式化工具：数字、时间、时长、JSON。
 * 所有输出对空值/异常值安全（返回 '-' 或原始内容），供列表与详情页复用。
 */

/** 大数字缩写：1234 -> 1.2K，2,000,000 -> 2.0M；空值/非数字返回 '-' */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const num = Number(value)
  if (Math.abs(num) >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M'
  if (Math.abs(num) >= 1_000) return (num / 1_000).toFixed(1) + 'K'
  return String(num)
}

/** 千分位整数（用于表格中精确展示行数等） */
export function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return Number(value).toLocaleString('zh-CN')
}

/**
 * 时间戳格式化。
 * - 秒级时间戳（>0 且 < 1e12）自动乘 1000 转毫秒；
 * - 已是毫秒的时间戳直接使用；
 * - 空值返回 '-'。
 */
export function formatDateTime(ts: number | string | null | undefined, withDate = true): string {
  if (ts === null || ts === undefined || ts === '') return '-'
  let num = Number(ts)
  if (Number.isNaN(num)) return String(ts)
  if (num > 0 && num < 1e12) num *= 1000
  if (num <= 0) return '-'
  const date = dayjs(num)
  if (!date.isValid()) return '-'
  return withDate ? date.format('MM-DD HH:mm') : date.format('HH:mm:ss')
}

/** 完整日期时间（用于详情页） */
export function formatFullDateTime(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === '') return '-'
  let num = Number(ts)
  if (Number.isNaN(num)) return String(ts)
  if (num > 0 && num < 1e12) num *= 1000
  if (num <= 0) return '-'
  const date = dayjs(num)
  return date.isValid() ? date.format('YYYY-MM-DD HH:mm:ss') : '-'
}

/** 秒数 → 人类可读时长：90 -> 1分30秒，7200 -> 2小时，空/0 返回 '-' */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return '-'
  const total = Math.max(0, Math.floor(Number(seconds)))
  if (total === 0) return '-'
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (days > 0) return `${days}天${hours}小时`
  if (hours > 0) return `${hours}小时${minutes}分`
  if (minutes > 0) return `${minutes}分${secs}秒`
  return `${secs}秒`
}

/**
 * 安全解析 JSON：非法 JSON 返回 undefined 而不是抛错。
 * 表单中的高级参数输入框使用，调用方据此提示校验错误。
 */
export function tryParseJSON(text: string): Record<string, any> | undefined {
  if (!text || !text.trim()) return undefined
  try {
    const parsed = JSON.parse(text)
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, any>)
      : undefined
  } catch {
    return undefined
  }
}

/** 截断长文本，超出部分以 … 结尾（用于表格单元格与日志摘要） */
export function truncate(text: string, max = 60): string {
  if (!text) return ''
  return text.length > max ? text.slice(0, max) + '…' : text
}
