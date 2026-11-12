/** Public download information; shared by the mirror pages and integration guide. */
export const resourceEndpoints = [
  ['游戏核心', '/mc/game/ · /v1/packages/ · /v1/objects/', 'Mojang', 'core'],
  ['加载器', '/maven/ · /fabric-meta/ · /quilt-meta/…', 'Forge / Fabric / Quilt / NeoForged', 'loaders'],
  ['模组与资源', '/mods/modrinth/ · /mods/curseforge/', 'Modrinth / CurseForge', 'mods'],
  ['Java 运行时', '/java/v1/…', 'Mojang', 'java'],
  ['游戏依赖', '/maven/…', 'Mojang', 'libraries'],
  ['原版资源', '/assets/{前两位}/{SHA-1}', 'Mojang', 'assets'],
];

export const resourceDownloadNotice = '模组、资源包、光影包和整合包按文件发布许可与下载策略提供缓存或跳转；可按官方排行提前缓存，也可固定项目或版本。具体接入路径见开发文档。';
