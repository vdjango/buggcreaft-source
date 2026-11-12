import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config

from bugg_source.collector import Collector
from bugg_source.config import Settings
from bugg_source.database import make_engine
from bugg_source.repository import Repository
from bugg_source.storage import ArtifactStore

ROOT = Path(__file__).resolve().parents[1]


def checkpoint() -> None:
    pass


def jar_bytes(members: dict[str, str | bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return stream.getvalue()


def fabric(**overrides: object) -> str:
    return json.dumps(
        {
            "schemaVersion": 1,
            "id": "actual_mod",
            "name": "包内真实名",
            "version": "1.2.3",
            **overrides,
        }
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        admin_token="test-token",
    )


@pytest.fixture
def repository(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BUGG_DATABASE_URL", settings.database_url)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = make_engine(settings.database_url)
    yield Repository(engine)
    engine.dispose()


def fake_collector(settings: Settings, payload: bytes, **limits: int) -> Collector:
    configuration = replace(settings, **limits)

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(payload))

    store = ArtifactStore(configuration, httpx.MockTransport(handle))
    return Collector(configuration, store)
