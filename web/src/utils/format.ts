/** 数字与金额格式化（全站统一） */

/** 金额（元）→ 万元，固定两位小数。null → — */
export function fmtYuan(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${(v / 10000).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 万元`
}

/** 金额（元）→ 万元，固定两位小数 */
export function fmtYuanShort(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return fmtYuan(v)
}

/** 数量：去掉多余小数尾零 */
export function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

/** 空值统一显示 */
export function orDash(v: string | null | undefined): string {
  return v && v.trim() ? v : '—'
}

/** 指标值智能格式化：键名含"金额/价"走金额短写，否则千分位 */
export function fmtMetric(key: string, v: number | string): string {
  if (typeof v === 'string') return v
  if (/金额|价/.test(key)) return fmtYuanShort(v)
  return v.toLocaleString('zh-CN')
}
