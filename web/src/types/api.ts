/** 通用 API 类型 */
export interface Page<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export type RequestState = 'idle' | 'loading' | 'success' | 'error'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** 后端 HTTPException 的 detail 原文（对象时带上，供调用方读结构化字段） */
    public detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** /api/health：给"资料库有没有更新"的探测用 */
export interface Health {
  ok: boolean
  announcements: number
  /** 变了就说明有新结果入库；内容本身没有意义 */
  data_version: string
}
