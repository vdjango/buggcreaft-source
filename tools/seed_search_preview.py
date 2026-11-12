"""Labelled local search corpus; no production credentials, data or upstream API."""

import argparse
import asyncio
import json
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from bugg_source.search.inference import model_manifest
from mc_translate.hashing import digest, resource_ref, text_hash
from mc_translate.search_export import SearchExport
from mc_translate.store import Store

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "data/search-preview"
CORPUS = [
    (
        "Storage01",
        "收纳工坊",
        "将箱子和背包中的物品按照类型自动分类，把相同的材料放在一起。支持铜制漏斗、快捷整理与容器排序。",
        "把乱七八糟的东西归到对应格子里",
    ),
    (
        "Map01",
        "远行手册",
        "在屏幕角落显示小地图，标记探索过的地形和当前位置，保存死亡地点的坐标。",
        "出门冒险时记住回家的路线",
    ),
    (
        "Light01",
        "光影画卷",
        "为水面加入真实反射，为森林加入柔和阴影与体积光。夕阳下的天空拥有绚丽的云层。",
        "让河流倒映天空并呈现漂亮的光照",
    ),
    (
        "Farm01",
        "田园笔记",
        "成熟的作物可以右键收获并自动补种，减少反复播种小麦和胡萝卜的时间。",
        "庄稼长好了就把果实取下来再种回去",
    ),
    (
        "Build01",
        "营造图鉴",
        "预览建筑蓝图的投影，逐层显示待放置方块和需要准备的材料清单。",
        "按照设计方案搭一座房子",
    ),
    (
        "Storage02",
        "仓储助手",
        "为多个储物容器提供统一的物品检索和整理入口，允许将同类物资快速归纳到对应箱子。",
        "整理仓库",
    ),
]


async def seed():
    store = await Store(str(DIRECTORY / "platform/translations.sqlite3")).open()
    await SearchExport(store).open()
    try:
        for identifier, title, body, _ in CORPUS:
            fields = {}
            for name, value in [
                ("title", title + " · 本地验收"),
                ("summary", "本地搜索验收资源，不代表真实上游项目。"),
                ("body", "# 资源介绍\n\n" + body + "\n\n此正文不构成 JAR 包内名称或依赖证据。"),
            ]:
                fields[name] = dict(
                    text=value,
                    format="markdown",
                    source_language="zh-Hans",
                    source_hash=text_hash(value, name, "markdown"),
                )
            await store.save_source(
                dict(
                    resource=resource_ref("modrinth", identifier),
                    kind="shader" if identifier == "Light01" else "mod",
                    source_url="https://modrinth.com/project/" + identifier,
                    fields=fields,
                    snapshot_hash=digest(fields),
                    metadata_hash=digest(identifier),
                    metadata=dict(
                        slug=identifier.lower(),
                        license="本地测试夹具",
                        releases=[
                            dict(
                                id="test-1",
                                name="1.0.0",
                                game_versions=["1.21.1"],
                                loaders=["fabric"] if identifier != "Light01" else [],
                                dependencies=[],
                            )
                        ],
                    ),
                )
            )
        (DIRECTORY / "queries.json").write_text(
            json.dumps(
                [dict(project_id="modrinth:" + row[0], query=row[3]) for row in CORPUS[:5]],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url", required=True, help="An isolated disposable PostgreSQL database"
    )
    args = parser.parse_args()
    if not args.database_url.startswith("postgresql+"):
        parser.error("use an isolated PostgreSQL database")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    model = ROOT / "data/models/bge-small-zh-v1.5"
    revision = model_manifest(model)["revision"]
    values = dict(
        BUGG_DATABASE_URL=args.database_url,
        BUGG_DATA_DIR=(DIRECTORY / "facts").as_posix(),
        BUGG_ADMIN_TOKEN="local-search-api-test",
        BUGG_PLATFORM_AUTH_TOKEN="local-search-mesh-test",
        BUGG_SEARCH_ENABLED="1",
        BUGG_SEARCH_SOURCE_URL="http://127.0.0.1:18448",
        BUGG_EMBEDDING_URL="http://127.0.0.1:18452",
        BUGG_EMBEDDING_TOKEN="local-search-model-test",
        BUGG_EMBEDDING_REVISION=revision,
        BUGG_EMBEDDING_DIMENSIONS="512",
        BUGG_EMBEDDING_MODEL_DIR=model.as_posix(),
        BUGG_PUBLIC_URL="http://127.0.0.1:15175",
        TRANSLATION_DATABASE=(DIRECTORY / "platform/translations.sqlite3").as_posix(),
        TRANSLATION_MIRROR_ROOT=(DIRECTORY / "mirror").as_posix(),
        TRANSLATION_ADMIN_TOKEN="local-search-admin-test",
        TRANSLATION_PUBLIC_URL="http://127.0.0.1:15175",
    )
    path = DIRECTORY / "preview.env"
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )
    from mc_translate.config import load_env

    load_env(path)
    command.upgrade(AlembicConfig(str(ROOT / "alembic.ini")), "head")
    asyncio.run(seed())
    print("Isolated search fixture ready: data/search-preview/preview.env")


if __name__ == "__main__":
    main()
