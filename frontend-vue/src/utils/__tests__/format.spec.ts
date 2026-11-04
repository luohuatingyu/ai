import { describe, it, expect } from 'vitest'
import {
  formatNumber, formatInteger, formatDateTime, formatFullDateTime,
  formatDuration, tryParseJSON, truncate,
} from '../format'

describe('formatNumber', () => {
  it('正常数字按千/百万缩写', () => {
    expect(formatNumber(0)).toBe('0')
    expect(formatNumber(999)).toBe('999')
    expect(formatNumber(1234)).toBe('1.2K')
    expect(formatNumber(1_500_000)).toBe('1.5M')
  })

  it('边界值：1000 与 1000000 恰好跨档', () => {
    expect(formatNumber(1000)).toBe('1.0K')
    expect(formatNumber(1_000_000)).toBe('1.0M')
  })

  it('空值与非法输入返回 "-"', () => {
    expect(formatNumber(null)).toBe('-')
    expect(formatNumber(undefined)).toBe('-')
    expect(formatNumber(Number.NaN)).toBe('-')
  })
})

describe('formatInteger', () => {
  it('千分位格式化', () => {
    expect(formatInteger(1234567)).toBe('1,234,567')
    expect(formatInteger(0)).toBe('0')
  })

  it('空值返回 "-"', () => {
    expect(formatInteger(null)).toBe('-')
    expect(formatInteger(undefined)).toBe('-')
  })
})

describe('formatDateTime / formatFullDateTime', () => {
  it('秒级时间戳自动转换为可读时间', () => {
    // 1700000000 秒（毫秒级 < 1e12 判定边界内）
    expect(formatDateTime(1700000000)).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(formatFullDateTime(1700000000)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  })

  it('毫秒级时间戳直接使用', () => {
    expect(formatDateTime(1_700_000_000_000)).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
  })

  it('空值与 0 返回 "-"', () => {
    expect(formatDateTime(0)).toBe('-')
    expect(formatDateTime(null)).toBe('-')
    expect(formatDateTime(undefined)).toBe('-')
    expect(formatDateTime('')).toBe('-')
  })
})

describe('formatDuration', () => {
  it('按时长分档展示', () => {
    expect(formatDuration(0)).toBe('-')
    expect(formatDuration(59)).toBe('59秒')
    expect(formatDuration(90)).toBe('1分30秒')
    expect(formatDuration(3600)).toBe('1小时0分')
    expect(formatDuration(86400 * 2 + 3600 * 3)).toBe('2天3小时')
  })

  it('空值与负数安全处理', () => {
    expect(formatDuration(null)).toBe('-')
    expect(formatDuration(undefined)).toBe('-')
    expect(formatDuration(-5)).toBe('-') // 负数钳制为 0 → "-"
  })
})

describe('tryParseJSON', () => {
  it('合法 JSON 对象返回解析结果', () => {
    expect(tryParseJSON('{"columns":["age"]}')).toEqual({ columns: ['age'] })
    expect(tryParseJSON('  {}  ')).toEqual({})
  })

  it('非法 JSON / 数组 / 空串返回 undefined', () => {
    expect(tryParseJSON('{invalid')).toBeUndefined()
    expect(tryParseJSON('[1,2]')).toBeUndefined()
    expect(tryParseJSON('')).toBeUndefined()
    expect(tryParseJSON('null')).toBeUndefined()
  })
})

describe('truncate', () => {
  it('超长文本截断并追加省略号', () => {
    expect(truncate('a'.repeat(80), 60)).toHaveLength(61)
    expect(truncate('a'.repeat(80), 60)).toMatch(/…$/)
    expect(truncate('short', 60)).toBe('short')
    expect(truncate('', 60)).toBe('')
  })
})
