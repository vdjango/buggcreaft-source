"""Create isolated, explicitly labelled preview data; never read a production database."""

import asyncio
import json
import os
from pathlib import Path
from zipfile import ZipFile

from alembic import command
from alembic.config import Config as AlembicConfig

from bugg_source.collector import Collector
from bugg_source.config import Settings
from bugg_source.database import make_engine
from bugg_source.repository import Repository
from mc_translate.hashing import digest, resource_ref, text_hash
from mc_translate.store import Store

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "data" / "migration-preview"


async def seed() -> None:
    PREVIEW.mkdir(parents=True, exist_ok=True)
    database = PREVIEW / "facts.sqlite3"
    os.environ["BUGG_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    command.upgrade(AlembicConfig(str(ROOT / "alembic.ini")), "head")
    settings = Settings(database_url=os.environ["BUGG_DATABASE_URL"], data_dir=PREVIEW / "facts")
    jar = PREVIEW / "visual-fixture.jar"
    with ZipFile(jar, "w") as archive:
        archive.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": "visual_fixture",
                    "version": "1.0.0",
                    "name": "视觉验收模组（测试包内名称）",
                    "environment": "*",
                    "depends": {"minecraft": ">=1.21", "fabricloader": ">=0.16"},
                },
                ensure_ascii=False,
            ),
        )
    engine = make_engine(settings.database_url)
    try:
        result = Collector(settings).import_local(jar, lambda: None)
        Repository(engine, settings=settings).save_local_scan(result)
        (PREVIEW / "digest.txt").write_text(result.artifact_sha256, encoding="ascii")
    finally:
        engine.dispose()
    store = await Store(str(PREVIEW / "platform" / "translations.sqlite3")).open()
    try:
        fields = {}
        for name, text, format in [
            ("title", "视觉验收资源 · 非生产数据", "plain"),
            ("summary", "仅供本地迁移验收，核对中文、正文与页面布局。", "plain"),
            (
                "body",
                "# 本地验收资源\n\n用于检查中文正文、版本与来源。\n\n"
                "此介绍不构成 JAR 名称或依赖证据。",
                "markdown",
            ),
        ]:
            fields[name] = dict(
                text=text,
                format=format,
                source_language="zh-Hans",
                source_hash=text_hash(text, name, format),
            )
        await store.save_source(
            dict(
                resource=resource_ref("modrinth", "Visual01"),
                kind="mod",
                source_url="https://modrinth.com/project/Visual01",
                fields=fields,
                snapshot_hash=digest({n: f["source_hash"] for n, f in fields.items()}),
                metadata_hash=digest("visual-preview-v1"),
                metadata={
                    "slug": "visual-fixture",
                    "authors": [{"name": "本地测试夹具"}],
                    "game_versions": ["1.21"],
                    "loaders": ["fabric"],
                    "license": "测试数据",
                    "releases": [
                        {
                            "name": "1.0.0",
                            "game_versions": ["1.21"],
                            "loaders": ["fabric"],
                            "dependencies": [],
                        }
                    ],
                },
            )
        )
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(seed())
