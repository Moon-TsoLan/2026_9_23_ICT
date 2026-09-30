/** 星图标签规则：画面只放"够短且不歧义"的文字，全名一律走悬停卡片。 */
import type { GraphNode } from '@/types/graph'

/** 只剥"公司族"后缀；医院/大学/中心/研究所是身份的一部分，保留 */
const ORG_SUFFIX = /(股份有限公司|集团有限公司|有限责任公司|分公司|有限公司|集团|公司)$/
const GEO_PREFIX = /^[\u4e00-\u9fa5]{1,4}?(省|市|自治区|自治州|地区|县|区)/
const PAREN = /[（(][^）)]*[）)]/g

/** 项目：只给「包N」定位码；取不到就不给文字（项目永远是证据，不是答案） */
export function projectCode(label: string): string {
  const m = label.match(/包\s*([0-9A-Za-z]{1,3})\s*$/)
  if (m) return `包${m[1]}`
  const g = label.match(/([0-9A-Za-z])\s*包\s*$/)
  if (g) return `包${g[1]}`
  return ''
}

/** 主体短名：去括号内地名 → 去公司族后缀 → 过长才剥地名前缀 */
export function orgShort(label: string): string {
  let s = label.replace(PAREN, '').trim()
  s = s.replace(ORG_SUFFIX, '')
  if (s.length > 8) {
    const noGeo = s.replace(GEO_PREFIX, '')
    if (noGeo.length >= 4) s = noGeo
  }
  if (s.length > 8) s = s.slice(0, 7) + '…'
  return s
}

/** 星图上要显示的文字；空串 = 这个节点此刻不配拥有文字 */
export function starText(node: GraphNode): string {
  if (node.labelShort !== undefined) return node.labelShort
  return node.kind === 'project' ? projectCode(node.label) : orgShort(node.label)
}

const CIRCLED = '①②④⑤⑥⑧⑨⑩⑫⑬⑮⑯⑰⑲⑳'

/** 排名并入标签前缀，不再做会飘走的独立徽标 */
export function rankMark(rank?: number): string {
  if (!rank || rank < 1) return ''
  return rank <= 20 ? CIRCLED[rank - 1]! : `#${rank}`
}

/** 结尾不可劈开的词尾：断行时整体留给下一行 */
const TAIL_WORDS = [
  '人民医院', '中心医院', '研究院', '研究所', '大学', '学院', '学校', '医院', '集团', '公司',
  '中心', '银行', '事务所', '管理局', '委员会', '服务站', '平台', '系统',
  '科技', '信息', '电子', '医疗', '软件', '工程', '设备', '改造', '建设', '升级', '维护', '采购',
]

/** 两行断行：宽度减半是"不把名字横向排开"的关键，但断点要尽量落在词界上 */
export function wrapTwo(text: string): string[] {
  if (text.length <= 5) return [text]
  const mid = Math.ceil(text.length / 2)
  let cut = mid
  for (const w of TAIL_WORDS) {
    const at = text.length - w.length
    if (at >= 2 && text.endsWith(w) && Math.abs(at - mid) <= 3) {
      cut = at // 词尾整体留给第二行
      break
    }
  }
  return [text.slice(0, cut), text.slice(cut)]
}

/** 保留地名的长版短名（只在短名会撞车时才用） */
function orgLong(label: string): string {
  const s2 = label.replace(PAREN, '').trim().replace(ORG_SUFFIX, '')
  return s2.length > 12 ? s2.slice(0, 11) + '…' : s2
}

/** 一组节点 → 各自星图上要显示的文字；短名撞车时逐级加长直到可区分 */
export function disambiguate(nodes: GraphNode[]): Map<string, string> {
  const out = new Map<string, string>()
  const groups = new Map<string, GraphNode[]>()
  for (const n of nodes) {
    const t = starText(n)
    out.set(n.id, t)
    if (!t) continue
    const g = groups.get(t)
    if (g) g.push(n)
    else groups.set(t, [n])
  }
  for (const [t, list] of groups) {
    if (list.length < 2) continue
    for (const n of list) {
      if (n.kind === 'project') continue // 项目包号在总览里没有上下文，交给场景视图
      const alt = orgLong(n.label)
      if (alt !== t) out.set(n.id, alt)
    }
  }
  return out
}
