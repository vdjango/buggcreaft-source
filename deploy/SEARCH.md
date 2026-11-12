# 正文与语义搜索部署

先读 [第 32 号契约](../文档/32-搜索实现与多节点契约.md)。搜索默认关闭；SQLite 不支持此功能。正文来自本仓库的目录快照/当前译文，不绕过来源授权，也不把正文写为包内事实。

## 数据库与进程

compose/CI 使用 pgvector/pgvector:0.8.2-pg18-trixie。生产升级前备份。由迁移账户安装 vector、pg_trgm 扩展并运行 `bugg-source --env .env db-upgrade`，运行账户只需业务表读写。已有普通 PostgreSQL 镜像必须先安装兼容的 pgvector 软件，不能只改连接字符串。0005 在 SQLite 上不创建搜索表；从已有 SQLite 切到 PostgreSQL 是独立数据迁移，不是自动复制。

同一目录只运行一个 hub 写自己的 SQLite。搜索 API 副本、索引 worker 共用同一个 PostgreSQL 主库，不使用只读副本处理分页/租约。开启 BUGG_SEARCH_ENABLED=1，设置同一 BUGG_PLATFORM_AUTH_TOKEN。统一 platform 入口会传递源站/API 回环地址并增加一个 search-worker；不额外运行第二个相同 supervisor。额外 worker 使用同版本安装、同 PG、同源站、不同 BUGG_NODE_ID，独立执行：

```powershell
.venv/Scripts/bugg-source.exe --env .env search-worker
# 只执行一轮；失败仍需查后台状态，不表示所有索引均完成。
.venv/Scripts/bugg-source.exe --env .env search-worker --once
```

其他主机通过固定 HTTPS 私网地址访问 hub 导出及推理。参考 search-private.conf：私网网关只允许可信网段，TLS 终结后转发至同机回环 hub，保留应用检查的内部密钥。hub 看到回环来源，但网关仍须执行私网限制；公开 Nginx 必须继续拒绝 /_internal/。如果自定义运行方式直接给 aiohttp 提供 TLS 且来源不是回环，还必须显式设置 BUGG_SEARCH_ALLOW_REMOTE_EXPORT=1，不能用不可信 X-Forwarded-Proto 冒充 TLS。API 查询节点通过该私网导出地址批量复核下架。

## 显式准备中文模型

在独立推理环境安装可选锁文件；API/索引 worker 不需要 ONNX 依赖：

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-embedding.lock
.venv/Scripts/python.exe tools/prepare_search_model.py --revision 46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59
```

默认从官方 Hugging Face 取 Qdrant 的 BAAI/bge-small-zh-v1.5 ONNX 文件；命令要求不可变提交，生成五个文件的 SHA-256 及 bugg-model.json。操作员可显式指定可信镜像 --endpoint；本地验收因官方连接不可达而显式使用 https://hf-mirror.com，没有在运行时回退下载。哈希清单保证后续运行一致，不替代首次下载渠道审核。

把清单中的 revision 配到 API/worker 的 BUGG_EMBEDDING_REVISION，维度 512。给推理服务设置相同 BUGG_EMBEDDING_TOKEN、BUGG_EMBEDDING_MODEL_DIR，并给 API/worker 设置固定 BUGG_EMBEDDING_URL。目录不得指向用户任意路径。运行时核对整个清单、local_files_only，不下载模型：

```powershell
.venv/Scripts/bugg-source.exe --env .env embedding-serve
```

默认回环 18452，CPU 两线程、最多两次并发、每批最多 16 段。跨机监听/证书由明确部署的私网 TLS 网关承担，不暴露模型端点到公网。文本/查询有字符和 token 上限，模型不符或向量非法拒绝。纯语义不可用返回 503；综合模式退回词法并标 degraded。

## 查询、镜像与扩容

公开网页走 /api/v1/search 和 /api/v1/projects/{provider:id}/similar，由 Nginx/Vite 映射至 hub 的 /v1/search 路径；hub 只调用自己的固定回环 FastAPI。批准镜像复用联邦认证转发同一路径，不缓存搜索响应、不传用户 Cookie/管理令牌、不运行模型。多个搜索 API 副本在 hub 本机可通过回环负载均衡入口接入；独立主机 API 用自己的私网入口并共享 PG/源站/模型配置，不公开 hub 内部端点。

搜索自身不提供跨独立目录 hub 的结果合并，也不支持多 hub 共同写一个库。公开 Nginx 仅信任它实际设置的 X-Real-IP；设置 TRANSLATION_TRUSTED_PROXY=true 前必须限制平台端口。hub 向 API 传递带内部密钥的哈希客户端身份；API 忽略未认证的身份头。共享 PG 限额为普通身份 120 次/分钟、批准镜像聚合身份 1200 次/分钟；镜像自身还有现有入口限额。直连 API 不接受无密钥的自报身份，生产应通过可信入口访问。

## 重建、升级与恢复

同一部署所有 API/worker 必须使用同一分词/切块/模型 revision。升级先停旧 worker，更新全部服务配置，再重建，禁止混合不同模型的 worker 争抢同一任务。管理页「作业管理 → 搜索索引作业」或 CLI `search-rebuild` 排队重建；逐项目原子替换，当前配置未完成的项目暂不返回。尚无双代次无中断切换。

摄取租约 60 秒、项目租约 120 秒，worker 每推理批次续约；领取 token 和修订阻止旧 worker 发布。失败间隔 30 秒，最多五次；重建可重新排队。硬退出等待租约，正常退出释放 HTTP/数据库连接。目录 epoch 改变会停止同步并拒绝旧查询；只有明确换权威库时，停旧 worker 后用 `search-rebuild --reset-source` 清空搜索投影、从新权威全量摄取。此参数不修改 JAR 事实或目录本身，旧分页会话失效。

PG 搜索六表由 0005 和 SearchRepository 唯一拥有，勿用 Base.metadata.create_all 管理；后续迁移手写并审核 SQL，不用 autogenerate 删除这些投影表。SQLite 的 search_* 触发器/三表由 SearchExport 管理，备份目录库时一起备份 epoch/事件。事件暂无自动压缩；需监控体积，在设计带快照水位的压缩协议前不能自行删事件。

当前向量使用精确余弦扫描，没有 HNSW/IVFFlat；词法/名称使用 GIN。查询数据库超时 2.5 秒、候选最多 200 项、会话五分钟、返回最多 50 项。大规模前用真实长正文、语言分布和并发测 P95/召回；未达到规模目标前不承诺百万库低延迟。优化优先加经过召回验证的 ANN/索引与容量设计，不凭 Python 标签改写 C++。
