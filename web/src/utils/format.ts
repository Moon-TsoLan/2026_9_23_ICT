/** 数字与金额格式化（全站统一） */

/** 金额（元）→ ¥1,486,000；有小数时保留到分。null → — */
export function fmtYuan(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  // 取整到元会让 2.64 × 9 = 23.76 显示成「¥3 × 9 = ¥24」，看上去像抽取错了。
  return `¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

/** 金额（元）→ 中文短写：1.40 亿 / 9,844 万 / ¥320.5 */
export function fmtYuanShort(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(1)} 万`
  return `¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
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
