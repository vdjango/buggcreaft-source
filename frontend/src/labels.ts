// Display labels only; raw declarations and API enum values stay unchanged.
export const stateLabels: Record<string, string> = {
  queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败', cancelled: '已取消',
  complete_declared: '声明已完整采集', partial: '部分采集', unsupported: '暂不支持',
  expected_hash_matched: '摘要已核验', unverified: '未提供预期摘要',
}

export const formatGiB = (value = 0) => `${(value / 1024 ** 3).toFixed(2)} GiB`
export const formatMiB = (value: number) => `${(value / 1024 ** 2).toFixed(2)} MiB`

export const errorLabels: Record<string, string> = {
  search_requires_enabled_postgresql: '搜索服务尚未启用，请联系站点管理员。',
  search_authority_unavailable: '暂时无法确认资源的发布状态，请稍后重试。',
  semantic_unavailable: '相近含义搜索暂不可用，请改用文字匹配或稍后重试。',
  search_database_unavailable: '搜索数据库暂不可用，请稍后重试。',
  search_session_expired_or_invalid: '本次结果已过期，请重新搜索。',
  search_rate_limited: '搜索过于频繁，请稍后重试。',
  unauthorized: '管理令牌无效，请重新连接。',
  admin_token_not_configured: '服务端尚未配置管理令牌。',
  scan_not_found: '未找到该制品的扫描记录。',
  job_not_found: '未找到该采集任务。',
  job_not_retryable: '该任务当前不能重试，请刷新状态。',
  invalid_artifact_cursor: '分页位置已失效，请返回列表首页。',
  collection_queue_full: '采集队列已满，请稍后再试。',
}
