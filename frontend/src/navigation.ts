/** Management-only navigation, shared by every admin route. */
export const adminSections = [
  { id: 'content', label: '内容管理', href: '/admin/mirror/resources', items: [
    { href: '/admin/mirror/resources', label: '缓存资源' },
    { href: '/admin/mirror/runtimes', label: '加载器与核心' },
    { href: '/admin/mirror/temporary', label: '客户端临时文件' },
    { href: '/admin/mirror/sources', label: '资源索引' },
    { href: '/content/artifacts', label: '制品库' }, { href: '/admin/feedback', label: '译文反馈' },
  ] },
  { id: 'jobs', label: '作业管理', href: '/admin/tasks', items: [
    { href: '/admin/tasks', label: '翻译任务' }, { href: '/operations/collections', label: '依赖采集' },
    { href: '/operations/search/jobs', label: '搜索索引作业' },
    { href: '/admin/mirror/tasks', label: '同步任务' }, { href: '/admin/mirror/transfers', label: '云迁移任务' },
  ] },
  { id: 'monitor', label: '运维监控', href: '/admin', items: [
    { href: '/admin', label: '运行总览' }, { href: '/admin/mirror/quality', label: '来源质量' },
    { href: '/operations/storage', label: '依赖存储与额度' },
    { href: '/operations/search/status', label: '搜索索引状态' },
  ] },
  { id: 'access', label: '节点与接入', href: '/admin/nodes', items: [
    { href: '/admin/nodes', label: '节点管理' }, { href: '/admin/clients', label: '客户端密钥' },
  ] },
  { id: 'settings', label: '系统设置', href: '/admin/mirror/settings/downloads', items: [
    { href: '/admin/mirror/settings/downloads', label: '下载策略' },
    { href: '/admin/mirror/settings/sources', label: '上游来源' },
    { href: '/admin/mirror/settings/quality', label: '选源规则' },
    { href: '/admin/mirror/settings/cloud', label: '镜像存储' },
    { href: '/admin/mirror/settings/storage', label: '同步与分流' },
    { href: '/admin/mirror/settings/room-cache', label: '客户端加速' },
    { href: '/admin/mirror/settings/cloudflare', label: '域名与 CDN' },
    { href: '/admin/settings', label: '翻译配置' }, { href: '/admin/mail', label: '邮件通知' },
    { href: '/settings', label: '依赖采集配置' },
  ] },
] as const

export function isAdminPath(path: string): boolean {
  return path === '/admin' || path.startsWith('/admin/') ||
    path.startsWith('/content/') || path.startsWith('/operations/') || path === '/settings'
}

export function adminItemActive(path: string, href: string): boolean {
  return path === href || (href === '/content/artifacts' && path.startsWith(href + '/')) ||
    (href === '/operations/collections' && path.startsWith(href + '/'))
}
export function adminSectionFor(path: string): string {
  if (['/admin/mirror', '/admin/traffic', '/admin/usage'].includes(path)) return 'monitor'
  if (path === '/admin/mirror/settings') return 'settings'
  return adminSections.find(section => section.items.some(item => adminItemActive(path, item.href)))?.id ?? 'monitor'
}
