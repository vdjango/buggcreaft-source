"""Shader review signals preserve release identity and never assert runtime compatibility."""

from fastapi.testclient import TestClient

from bugg_source.api import create_app
from bugg_source.contracts import ReleaseIntake
from bugg_source.repository import Repository


def observation(release, digest, project="YL57xq9U", dependencies=None):
    return ReleaseIntake(
        provider="modrinth",
        project_id=project,
        release_id=release,
        url=f"https://cdn.modrinth.com/{release}.jar",
        digest=digest * 64,
        algorithm="sha256",
        size=100,
        game_versions=["26.1.1", "26.1.2"],
        loaders=["neoforge"],
        dependencies=dependencies or [],
    )


def test_review_is_persistent_scoped_and_does_not_infer_zero_dependencies(settings, repository):
    empty = observation("iris1", "a")
    repository.observe_release(empty)
    repository.observe_release(empty)
    repository.observe_release(observation("iris2", "b"))
    repository.observe_release(observation("unrelated", "c", project="other"))
    repository.observe_release(
        observation("has-dependency", "d", dependencies=[{"dependency_type": "required"}])
    )
    rows = Repository(repository.engine).shader_reviews("26.1.1", 100)
    assert len(rows) == 2
    assert Repository(repository.engine).shader_reviews("1.20.1", 100) == []
    assert {row["observation"]["release_id"] for row in rows} == {"iris1", "iris2"}
    assert all(row["state"] == "pending" and row["decisions"] == [] for row in rows)


def test_review_api_requires_admin_and_appends_decisions(settings, repository):
    repository.observe_release(observation("iris1", "a"))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/shader-reviews").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        row = client.get("/api/v1/shader-reviews?game_version=26.1.1", headers=headers).json()[
            "items"
        ][0]
        route = f"/api/v1/shader-reviews/{row['id']}/decisions"
        body = {
            "reviewer": "operator",
            "conclusion": "confirmed_missing",
            "note": "JAR requires sodium",
        }
        assert client.post(route, json=body).status_code == 401
        assert client.post(route, json={**body, "note": " "}, headers=headers).status_code == 422
        assert client.post(route, json=body, headers=headers).status_code == 200
        body["conclusion"] = "needs_investigation"
        assert client.post(route, json=body, headers=headers).status_code == 200
        row = client.get("/api/v1/shader-reviews", headers=headers).json()["items"][0]
        assert row["state"] == "pending"
        assert [decision["conclusion"] for decision in row["decisions"]] == [
            "confirmed_missing",
            "needs_investigation",
        ]


def test_curseforge_review_uses_registered_project_and_required_relation(repository):
    base = observation("iris1", "a").model_dump()
    base.update(provider="curseforge", project_id="455508")
    repository.observe_release(ReleaseIntake(**base))
    base.update(release_id="iris2", digest="b" * 64, dependencies=[{"relationType": 3}])
    repository.observe_release(ReleaseIntake(**base))
    rows = repository.shader_reviews("26.1.1", 100)
    assert len(rows) == 1
    assert rows[0]["observation"]["provider"] == "curseforge"
