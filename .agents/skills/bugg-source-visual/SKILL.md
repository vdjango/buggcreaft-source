---
name: bugg-source-visual
description: Verify Bugg Source Vue pages in a running browser and inspect screenshots after UI changes in E:/code/bugg-source.
---

# Bugg Source 页面视觉验收

用户要求每个完成的页面都必须实际打开、核对内容和布局，并由开发代理查看页面截图。类型检查、构建、DOM 断言不能代替查看图片。

- 先读项目 AGENTS.md、文档 08 与 20，复用现有页面、Pinia store 和 Axios 客户端。
- 启动本地 API 与 Vite；使用 Playwright 的浏览器执行已有 `frontend/tools/visual-check.mjs`，可设置 `BUGG_VISUAL_BASE_URL`、`BUGG_VISUAL_TOKEN`。令牌只从进程环境读取，不打印、不入库、不截取明文。
- 基础脚本逐页检查制品库、制品详情、采集任务、新建采集、存储额度及平台配置六页；完整迁入的平台使用 `npm run visual:migration`（frontend/tools/migration-visual.mjs），当前覆盖 34 条路由，以脚本实际清单为准。额外 `BUGG_VISUAL_EXTRA=1` 检查匿名登录、安装首步、正文/声明/表单底部及移动菜单；`BUGG_VISUAL_NAVIGATION=1` 实际点击五组后台菜单及全部子菜单，验证同一 SPA、目标地址、高亮及移动菜单收起。覆盖桌面和窄屏、未连接、空记录、连接失败，以及真实声明/依赖表。公开目录与搜索不能作为后台功能入口。HTTP 拦截场景必须标为测试数据，另保留真实本地 API 场景。
- 改版前读取当前启动器 DesignTokens 与实际截图；颜色和风景资产复用当前视觉来源。后台只允许平台 Router、SiteNav/AdminNav、AdminAccess 与共享根主题一个所有者，子页面不得另设外壳或根主题。检查全局 CSS 对管理内容的 margin、网格对齐与滚动影响，不能只检查页面自身样式。全局路由捕获器负责的导航行为在该所有者修复，避免复制到每个链接组件。
- 基础截图写入忽略的 `data/visual/`，完整迁移截图写入 `data/visual-migration/`。必须调用可用的图片查看工具逐张查看本次所有截图，检查中文、字段真实性、截断、溢出、按钮状态、空态和错误反馈；发现问题修复后重新生成并查看受影响图片。滚动表格需要实际核对内容；只截页首不能代表已检查页底。
- 页面切换和连接切换时，检查取消请求、停止轮询及过期响应；复用 Vitest 状态测试和浏览器断言。不能把缺少云配置、跳过 PostgreSQL 或未执行真实雨云上传标为验收通过。
- 将页面、视口、数据来源、图片路径、实际发现和修复写入第 27 或 31 号文档验收记录。无法打开浏览器或查看图片时，明确记为未完成视觉验收。

脚本入口属于项目功能索引维护范围；测试夹具与实际生产数据分开保存。
