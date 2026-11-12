# 当前平台部署入口

本目录是当前部署方案。reference/platform/deploy、scripts 仅是旧方案参考，不直接执行。先在本仓库根目录完成 Python 安装、frontend/npm ci 和 npm run build；所有 Vue 产物输出到 src/mc_translate/web，缺少构建会导致公开/后台页面不完整。保留 LICENSE、NOTICE 与 AGPL 源码提供义务。

正文全文/真实语义及多节点的开关、模型、共享 PG、镜像代理、重建和升级见 [SEARCH.md](SEARCH.md)。公开搜索经 hub/批准镜像再进入 API；nginx.conf 已单独分流。搜索使用带 pgvector 的 PostgreSQL，模型是独立可选推理进程，默认关闭，不自动下载。

## 首次安装（Windows 本地）

需要 Python 3.11+、Node.js/npm。在仓库根目录执行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe -m pip install -e . --no-deps
Copy-Item .env.example .env
cd frontend
npm ci
npm run build
cd ..
.venv/Scripts/bugg-source.exe --env .env db-upgrade
.venv/Scripts/bugg-source.exe --env .env platform --workers 1
```

启动前按 `.env.example` 配置数据目录与数据库。生产依赖事实库使用 PostgreSQL；本地单 worker 开发可使用 SQLite，平台目录库与事实库的边界见下文。不配置模型或云密钥也能启动并读取已有缓存，生成译文须另行配置服务商。

首次访问 `http://127.0.0.1:18448/setup` 配置管理员。本地完整同源访问方式见下文“本地开发”；直接访问 18448 不会转发依赖 API。`.env` 按字面读取，不执行脚本或展开变量；进程环境优先。`--env` 放在子命令前。

## 数据与配置

2026-11-10 的真实上线、原站接续、备份和未完成项见 [第 34 号](../文档/34-生产迁移与发布.md)。`bugg-source-production.conf` 是原 systemd 服务的末尾 drop-in，`bugg-source-embedding.service` 为独立模型；生产 API 使用 18451，保留已有 18449 下载服务。下表为新安装默认值。

从 .env.example 建立本项目配置，凭据放仓库外。原平台库 TRANSLATION_DATABASE 使用独立 SQLite 文件，不接受 PG URL；只运行一份 hub。事实 BUGG_DATABASE_URL 生产用 PostgreSQL，开发 SQLite 只支持单采集 worker。BUGG_DATA_DIR 必须在 API/worker 间一致；镜像根 TRANSLATION_MIRROR_ROOT 独立，不能把临时分发副本当事实 CAS 删除。先备份再升级：

```powershell
.venv/Scripts/bugg-source.exe --env .env db-upgrade
.venv/Scripts/bugg-source.exe --env .env platform --workers 1
```

统一入口自身不迁移数据库，任一子进程失败会清理同组进程；系统服务应监管该入口。当前源码目录为运行根，需使用可写 data 卷及同版本构建。示例 compose 仅数据库，尚不是完整容器部署。

## 端口和代理

| 内部端口 | 进程 | 归属 |
| --- | --- | --- |
| 18448 | mc_translate hub | HTML/静态 Vue、/v1、账户、译文 |
| 18449 | FastAPI | /api/v1 依赖事实和作业、/health |
| 18450 | mc_translate.mirror | 游戏镜像、Maven、Java、房间文件字节 |

三个端口保持回环私有。生产 Nginx 的 HTTPS server 中 include 本目录 nginx.conf；替换自身 server_name/证书，若改内部端口也要同步 proxy_pass。BUGG_PUBLIC_URL 和 TRANSLATION_PUBLIC_URL 必须为同一外部 origin（含非标准端口）。有且只有可信反向代理能访问平台内部口时，才配置 TRANSLATION_TRUSTED_PROXY=true。

Nginx 拒绝 /_internal/；`/_mirror_node/` 是权重分流使用的公开下载路径，直接进入镜像进程。每个代理 location 引用 `proxy-headers.conf` 安装后的 `/etc/nginx/snippets/bugg-source-proxy-headers.conf`，防止子层 proxy_set_header 使 Host/协议头继承失效。/api/v1 到 FastAPI，/v1 到平台；下载路径直接到镜像进程，禁 Python 应用间二次字节代理。公开 /dependency-health 仅证明事实数据库可访问，不证明 worker/云/译文全就绪。本次生产已执行 nginx -t 与真实 TLS；接口/登录/后台必须遵循 no-store、清除 CDN 旧副本，并验证匿名请求。

统一入口自动生成 BUGG_PLATFORM_AUTH_URL 与内部共享密钥并传给所有子进程。若分开启动，显式给 hub/API 相同 BUGG_PLATFORM_AUTH_TOKEN、给 API 固定回环 BUGG_PLATFORM_AUTH_URL。不能对公网开放该接口，也不能把内部密钥发前端。平台重启后管理会话失效；默认依赖后台自动尝试同源 Cookie，不持久化 Bearer。

## 本地开发

统一服务运行后，在 frontend 运行 npm run dev，默认 127.0.0.1:5173。Vite 按路径代理到真实三服务，HTML/会话由 hub 负责；先 npm run build，修改代码后重新构建，当前此接线模式不承诺源文件热更新。首次 /setup 建账号。平台 18448 直连可浏览平台，但 /api/v1 不会被该端口转发，完整依赖后台必须通过同源代理。

## 存储与发布验证

镜像复用原雨云/S3/七牛 profile 管理与空间账本，依赖归档使用独立前缀/预算和 archive-worker。不要同一个 key 被两种生命周期清理；真实雨云尚未测试。七牛通过可选依赖 qiniu；ACME 使用 acme extra，Windows 服务使用 windows extra，本机未安装/联调后二者不能算交付验证。

发布验证按第 10、17、31、34 号：真实 PG 并发/恢复、实际云 checksum/重试、模型/SMTP、TLS/代理、备份恢复与负载，以实际记录为准。默认关闭自动同步/来源扫描/质量采样，新安装需明确设置预算并开启，原站迁移保留已有设置；有目录列表不代表已扫描全站 JAR。本次生产原位接续数据与配置，凭据留在服务器，主站流量已切换；未测 SMTP、新归档上传与负载不算通过。

