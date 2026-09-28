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
  ) {
    super(message)
    this.name = 'ApiError'
  }
}
