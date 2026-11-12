<div align="center">

# Bugg 源站

**发现喜欢的 Minecraft 资源，读懂介绍，把文件下载到手。**

资源搜索 · 中文译文 · 游戏镜像 · 模组依赖信息

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-2F7D5C?style=flat-square)](LICENSE)
[![Minecraft Java Edition](https://img.shields.io/badge/Minecraft-Java_Edition-62A344?style=flat-square)](https://mirror.bugg.icu/)
[![Modrinth](https://img.shields.io/badge/Resources-Modrinth-1BD96A?style=flat-square)](https://mirror.bugg.icu/search)
[![CurseForge](https://img.shields.io/badge/Resources-CurseForge-F16436?style=flat-square)](https://mirror.bugg.icu/search)
[![Self-hosted](https://img.shields.io/badge/Deploy-Self--hosted-5478B9?style=flat-square)](deploy/README.md)

[**搜索资源**](https://mirror.bugg.icu/search) · [**浏览译文**](https://translate.bugg.icu/) · [**镜像服务**](https://mirror.bugg.icu/) · [**开发者接入**](https://mirror.bugg.icu/developers)

</div>

---

![Bugg 源站界面](docs/images/source-preview.png)

Bugg 源站是面向 Minecraft Java 版玩家与社区的资源服务。它将 Modrinth、CurseForge 的资源检索、项目介绍译文和文件分发放在一起，也为启动器提供统一的接入地址。

想找模组、挑光影，或看懂一个整合包的玩法？直接打开网页即可开始，无需先部署服务。

## 能为你做什么

| 你想做的事 | Bugg 源站提供的帮助 |
| --- | --- |
| 找到想玩的内容 | 搜索模组、资源包、光影包和整合包，结合游戏版本、加载器等条件筛选。 |
| 看懂外文介绍 | 阅读项目简介与正文的中文译文，随时对照原文、访问原作者页面。 |
| 减少下载时的等待 | 为游戏文件、加载器、Java 和模组资源提供镜像入口；按策略使用缓存或其他下载来源。 |
| 少一些临时回源 | 管理员可提前准备热门资源、加载器与光影核心，让后续请求复用缓存。 |
| 查清模组的依赖 | 对已采集的 JAR 读取包内声明，按具体文件记录依赖与版本约束，区分不同版本、加载器。 |

资源文件按发布许可与站点策略提供缓存或跳转。项目介绍的译文与原文分别保存，不改写作者发布的资源文件。

## 开始使用

1. 打开 [资源搜索](https://mirror.bugg.icu/search)，输入名称或关键词，选择需要的资源类型。
2. 查看项目介绍，确认目标 **Minecraft 版本与加载器**，再选择对应的内容版本。
3. 阅读译文或对照原文，通过项目提供的入口获取资源；启动器接入方式见 [开发者中心](https://mirror.bugg.icu/developers)。

只想看介绍，也可以从 [译文库](https://translate.bugg.icu/) 开始。译文尚未就绪时可先阅读原文，不必等翻译完成再了解项目。

## 关于下载与兼容性

**为什么有时从源站下载，有时跳转到其他地址？**  
源站会按缓存状态、来源策略和分发许可处理请求。命中可用缓存时提供缓存文件；未命中时可能回源或跳转，首次请求不一定已经有缓存。实际速度还取决于你的网络和上游状态。

**光影包和光影核心是同一个东西吗？**  
光影包提供效果，Iris、Oculus 等核心负责加载它们。安装时除了光影包的支持信息，还要核对核心、其他模组、游戏版本与加载器的组合。

**标着“支持这个游戏版本”，就一定能一起运行吗？**  
项目页面的版本标签适合筛选；具体文件还可能要求某个依赖版本，或与其他模组冲突。源站将包内声明与页面信息分开记录。已解析的依赖也不能替代实际运行验证，未采集或未完整解析的文件不会被当成“没有依赖”。

**下载、读取译文需要申请密钥吗？**  
公开文件下载和已有译文读取不需要客户端密钥。译文生成等能力按站点配置与额度开放，详见 [接入说明](https://mirror.bugg.icu/docs#authentication)。

## 自建源站与社区接入

你可以部署自己的服务，也可以让已有启动器接入 Bugg 源站。

- **自建服务**：管理资源、译文、缓存和下载来源；存储支持本地目录及兼容的对象存储，可按需要组合使用。
- **贡献镜像节点**：按节点接入流程登记与审核，分担公开内容读取；具体能力和权限见接入文档。
- **接入启动器**：使用公开接口读取项目与译文、映射下载地址，保留文件哈希校验、断点续传和跳转处理。

详细安装步骤集中在 [部署指南](deploy/README.md)。搜索增强是可选功能，配置方式见 [搜索部署指南](deploy/SEARCH.md)。

## 文档导航

| 想了解什么 | 从这里开始 |
| --- | --- |
| 在线找资源、看介绍 | [资源搜索](https://mirror.bugg.icu/search) · [译文库](https://translate.bugg.icu/) |
| 源站能分发哪些文件 | [镜像服务](https://mirror.bugg.icu/) |
| 启动器或社区服务接入 | [开发者中心](https://mirror.bugg.icu/developers) · [接口文档](https://mirror.bugg.icu/docs) |
| 自己部署与配置 | [部署指南](deploy/README.md) · [配置示例](.env.example) |
| 项目范围、架构与维护 | [文档总览](文档/00-规划总览.md) · [变更记录](文档/13-开发任务与变更记录.md) |

## 一起改进

欢迎反馈译文问题、失效下载，以及可以复现的资源兼容问题。为便于定位，请附上资源页面、游戏版本、加载器、具体文件版本和错误日志，分享前移除日志中的个人信息与凭据。

参与代码或文档改进前，请阅读 [协作约束](AGENTS.md) 与 [代码规范](文档/09-代码规范与复用门禁.md)。无论是修正一句译文、补充接入示例，还是改进缓存与依赖解析，都欢迎参与。

## 许可与致谢

本项目采用 **GNU AGPL-3.0-or-later**，详见 [LICENSE](LICENSE)。代码来源与第三方声明见 [NOTICE](NOTICE)；修改并通过网络提供服务时，请遵守许可证中的源码提供要求。

感谢 Minecraft 资源作者，以及 Modrinth、CurseForge 等社区。资源文件、项目介绍与图片的权利归各自作者所有，请遵守对应资源的许可与分发要求。
