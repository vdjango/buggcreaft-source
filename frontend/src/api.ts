import axios from 'axios'
import { errorLabels } from './labels'

export interface Job {
  id: string; state: string; attempts: number; error: string | null;
  artifact_sha256: string | null; cancel_requested: boolean; verification: string;
  parser_version: string; priority: number; cache_hit: boolean
}
export interface Dependency {
  target_id: string; relation: string; version_raw: unknown;
  environment: string | null; ordering: string | null; source_path: string
}
export interface ModFact {
  mod_id: string; declared_name: string | null; version_raw: string | null;
  version_resolved: string | null; loader: string; container_path: string;
  metadata_path: string; dependencies: Dependency[]
}
export interface Scan {
  artifact_sha256: string; parser_version: string; status: string;
  mods: ModFact[]; evidence: {member_path: string; sha256: string}[];
  issues: {code: string; path: string; detail: string}[]
}
export interface CollectRequest {
  url: string; expected_hash: string | null; hash_algorithm: string
}

export interface ArtifactSummary {
  scan_id: string; artifact_sha256: string; size: number; parser_version: string; status: string; created_at: string
}
export interface ArtifactPage { items: ArtifactSummary[]; next_cursor: string | null }

export interface StorageStatus {
  budget_day_utc: string; active: { queued?: number; running?: number };
  max_pending_jobs: number; max_running_jobs: number;
  daily_download_bytes: number; reserved_download_bytes: number; min_free_disk_bytes: number;
  cloud: { configured: boolean; provider?: string; profile_id?: string; states?: Record<string, number>;
    capacity_bytes?: number; reserved_capacity_bytes?: number; daily_upload_bytes?: number; reserved_upload_bytes?: number }
}

export const http = axios.create({ baseURL: '/api/v1', timeout: 15000 })
http.interceptors.response.use(response => response, (cause: unknown) => {
  if (axios.isCancel(cause)) return Promise.reject(cause)
  if (axios.isAxiosError(cause)) {
    const detail = cause.response?.data?.detail
    return Promise.reject(new Error(typeof detail === 'string' ? errorLabels[detail] ?? detail :
      cause.response ? `服务请求失败（${cause.response.status}）` : '无法连接服务，请检查网络后重试'))
  }
  return Promise.reject(new Error('服务请求失败'))
})
export const isCancelled = axios.isCancel

export async function request<T>(token: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await http.request<T>({ url: path, method: body === undefined ? 'GET' : 'POST',
    data: body, signal, headers: token ? { Authorization: `Bearer ${token}` } : {} })
  return response.data
}
