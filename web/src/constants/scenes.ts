/** 五大业务场景的元数据（单一来源）。
 *
 *  放在独立模块而不是组件里，因为 `<script setup>` 不允许 ES 导出，
 *  而场景元数据同时被 SceneBar（渲染芯片）与 ExploreView（决定查询语义）使用。 */
import type { SceneId } from '@/types/explore'

export interface SceneMeta {
  name: string
  desc: string
  /** 该场景的主体类型：S1/S2 是采购单位，S3/S4/S5 是中标供应商 */
  subjectKind: 'buyer' | 'supplier'
  /** 是否需要多家主体（只有 S4/S5） */
  multi: boolean
  hint: string
}

export const SCENES: Record<SceneId, SceneMeta> = {
  S1: { name: '长期合作', desc: '采购单位 → 合作的中标/产品供应商', subjectKind: 'buyer', multi: false, hint: '选 1 个采购单位' },
  S2: { name: '高频投标', desc: '采购单位 → 高频投标主体与协同组合', subjectKind: 'buyer', multi: false, hint: '选 1 个采购单位' },
  S3: { name: '共同竞标', desc: '中标供应商 → 高频共同竞标主体', subjectKind: 'supplier', multi: false, hint: '选 1 个中标供应商' },
  S4: { name: '交集采购', desc: '多中标商 → 共同合作的采购单位', subjectKind: 'supplier', multi: true, hint: '选 2 个以上中标供应商' },
  S5: { name: '交集项目', desc: '多中标商 → 共同竞标的项目', subjectKind: 'supplier', multi: true, hint: '选 2 个以上中标供应商' },
}

export const SCENE_IDS = Object.keys(SCENES) as SceneId[]
