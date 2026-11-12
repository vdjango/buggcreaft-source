import hashlib
import io
import json
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from conftest import checkpoint, fabric, fake_collector, jar_bytes

from bugg_source.collector import Collector
from bugg_source.contracts import Cancelled, CollectionError, CollectRequest
from bugg_source.storage import ArtifactStore

URL = "https://cdn.modrinth.com/example.jar"


def test_library_wrapper_keeps_evidence_but_identifies_inner_active_mod(settings):
    nested = jar_bytes(
        {
            "META-INF/neoforge.mods.toml": 'modLoader="javafml"\nloaderVersion="[4,)"\n[[mods]]\nmodId="actual_mod"\nversion="2"'
        }
    )
    payload = jar_bytes(
        {
            "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\r\nFMLModType: LIBRARY\r\n\r\n",
            "META-INF/neoforge.mods.toml": 'modLoader="javafml"\nloaderVersion="[4,)"\n[[mods]]\nmodId="actual_mod"\nversion="2"',
            "META-INF/jarjar/metadata.json": json.dumps({"jars": [{"path": "nested/mod.jar"}]}),
            "nested/mod.jar": nested,
        }
    )
    result = fake_collector(settings, payload).collect(CollectRequest(url=URL), checkpoint)
    assert result.mods[0].container_type == "LIBRARY"
    assert result.mods[1].container_type is None
    assert result.mods[1].container_path == "root!/nested/mod.jar"


def test_download_extracts_real_name_dependencies_and_evidence(settings):
    raw = fabric(
        depends={"minecraft": [">=1.20", "1.19.4"], "library": "^2"},
        recommends={"nice": "*"},
        breaks={"bad": "*"},
        conflicts={"other": "<2"},
        suggests={"bonus": "*"},
    )
    payload = jar_bytes({"fabric.mod.json": raw})
    collector = fake_collector(settings, payload)
    result = collector.collect(CollectRequest(url=URL), checkpoint)
    assert result.status == "complete_declared"
    assert result.artifact_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.mods[0].declared_name == "包内真实名"
    dependencies = result.mods[0].dependencies
    assert {row.relation for row in dependencies} == {
        "depends",
        "recommends",
        "breaks",
        "conflicts",
        "suggests",
    }
    assert dependencies[0].version_raw == [">=1.20", "1.19.4"]
    evidence = result.evidence[0]
    extracted = settings.data_dir / "evidence" / evidence.object_key
    assert extracted.read_bytes() == raw.encode()
    assert hashlib.sha256(extracted.read_bytes()).hexdigest() == evidence.sha256
    assert (settings.data_dir / "blobs" / f"{result.artifact_sha256}.jar").read_bytes() == payload


def test_missing_name_does_not_fall_back_to_id_or_filename(settings):
    document = json.loads(fabric())
    del document["name"]
    result = fake_collector(settings, jar_bytes({"fabric.mod.json": json.dumps(document)})).collect(
        CollectRequest(url=URL), checkpoint
    )
    assert result.mods[0].declared_name is None
    assert result.mods[0].mod_id == "actual_mod"


def test_forge_multi_mod_and_manifest_placeholder(settings):
    raw = """modLoader="javafml"
loaderVersion="[47,)"
[[mods]]
modId="one"
displayName="First Real Name"
version="${file.jarVersion}"
[[mods]]
modId="two"
displayName="Second Real Name"
version="2.0"
[[dependencies.one]]
modId="forge"
mandatory=true
versionRange="[47,48)"
side="BOTH"
ordering="AFTER"
[[dependencies.two]]
modId="compat"
mandatory=false
versionRange="[1,)"
"""
    payload = jar_bytes(
        {
            "META-INF/mods.toml": raw,
            "META-INF/MANIFEST.MF": (
                "Manifest-Version: 1.0\r\nImplementation-Version: 1.2.\r\n 3\r\n\r\n"
                "Name: wrong\r\nImplementation-Version: 9\r\n"
            ),
        }
    )
    result = fake_collector(settings, payload).collect(CollectRequest(url=URL), checkpoint)
    assert result.status == "complete_declared"
    one, two = result.mods
    assert one.version_raw == "${file.jarVersion}"
    assert one.version_resolved == "1.2.3"
    assert one.dependencies[0].target_id == "forge"
    assert one.dependencies[0].version_raw == "[47,48)"
    assert two.dependencies[0].relation == "optional"
    assert one.loader_constraints == {"modLoader": "javafml", "loaderVersion": "[47,)"}


@pytest.mark.parametrize("relation", ["required", "optional", "incompatible", "discouraged"])
def test_neoforge_dependency_types(settings, relation):
    raw = f'''[[mods]]
modId="actual"
displayName="Actual"
version="1"
[[dependencies.actual]]
modId="other"
type="{relation}"
versionRange="[1,)"
'''
    result = fake_collector(settings, jar_bytes({"META-INF/neoforge.mods.toml": raw})).collect(
        CollectRequest(url=URL), checkpoint
    )
    assert result.mods[0].loader == "neoforge"
    assert result.mods[0].dependencies[0].relation == relation


def test_nested_jar_has_separate_identity_and_provenance(settings):
    nested = jar_bytes({"fabric.mod.json": fabric(id="embedded", name="Embedded")})
    payload = jar_bytes(
        {
            "fabric.mod.json": fabric(jars=[{"file": "META-INF/jars/inner.jar"}]),
            "META-INF/jars/inner.jar": nested,
        }
    )
    result = fake_collector(settings, payload).collect(CollectRequest(url=URL), checkpoint)
    assert result.status == "complete_declared"
    assert len(result.mods) == 2
    assert result.mods[1].container_path == "root!/META-INF/jars/inner.jar"
    assert not result.mods[0].dependencies
    assert any(row.sha256 == hashlib.sha256(nested).hexdigest() for row in result.evidence)


@pytest.mark.parametrize("metadata", ["quilt.mod.json", "mcmod.info"])
def test_unsupported_metadata_is_not_reported_as_zero_dependencies(settings, metadata):
    result = fake_collector(settings, jar_bytes({metadata: "{}"})).collect(
        CollectRequest(url=URL), checkpoint
    )
    assert result.status == "partial"
    assert result.issues[0].code == "unsupported_metadata_format"
    assert result.evidence


@pytest.mark.parametrize(
    "raw",
    ['{"id":"one","id":"two"}', "{broken", fabric(depends={"bad": 12}), fabric(schemaVersion=99)],
)
def test_invalid_metadata_never_becomes_complete(settings, raw):
    result = fake_collector(settings, jar_bytes({"fabric.mod.json": raw})).collect(
        CollectRequest(url=URL), checkpoint
    )
    assert result.status == "partial"
    assert result.issues


def test_missing_or_unsafe_nested_jar_is_visible(settings):
    payload = jar_bytes(
        {"fabric.mod.json": fabric(jars=[{"file": "../escape.jar"}]), "../escape.jar": b"evil"}
    )
    with pytest.raises(CollectionError, match="unsafe_archive_path"):
        fake_collector(settings, payload).collect(CollectRequest(url=URL), checkpoint)
    assert not (settings.data_dir.parent / "escape.jar").exists()


@pytest.mark.parametrize("payload", [b"not a jar", b"PK\x03\x04broken"])
def test_corrupt_jar_fails(settings, payload):
    with pytest.raises(CollectionError, match="invalid_jar_archive"):
        fake_collector(settings, payload).collect(CollectRequest(url=URL), checkpoint)


def test_download_size_and_hash_rejection_remove_partial_file(settings):
    payload = jar_bytes({"fabric.mod.json": fabric()})
    with pytest.raises(CollectionError, match="upstream_hash_mismatch"):
        fake_collector(settings, payload).collect(
            CollectRequest(url=URL, expected_hash="0" * 64), checkpoint
        )
    with pytest.raises(CollectionError, match="download_size_limit"):
        fake_collector(settings, payload, max_download_bytes=10).collect(
            CollectRequest(url=URL), checkpoint
        )
    assert not list((settings.data_dir / "tmp").iterdir())
    assert not list((settings.data_dir / "blobs").iterdir())


def test_metadata_extraction_budget(settings):
    payload = jar_bytes({"fabric.mod.json": fabric()})
    with pytest.raises(CollectionError, match="member_size_limit"):
        fake_collector(settings, payload, max_metadata_bytes=10).collect(
            CollectRequest(url=URL), checkpoint
        )


def test_duplicate_zip_entry_rejected(settings):
    stream = io.BytesIO()
    with pytest.warns(UserWarning), zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("fabric.mod.json", fabric())
        archive.writestr("fabric.mod.json", fabric(id="different"))
    with pytest.raises(CollectionError, match="duplicate_zip_entry"):
        fake_collector(settings, stream.getvalue()).collect(CollectRequest(url=URL), checkpoint)


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.modrinth.com/a.jar",
        "https://127.0.0.1/a.jar",
        "https://cdn.modrinth.com.evil.test/a.jar",
        "https://user:secret@cdn.modrinth.com/a.jar",
        "https://cdn.modrinth.com:8443/a.jar",
    ],
)
def test_url_policy(settings, url):
    with pytest.raises(CollectionError, match="download_url_not_allowed"):
        ArtifactStore(settings).validate_url(url)


def test_redirect_target_must_pass_same_policy(settings):
    seen = []

    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    collector = Collector(settings, ArtifactStore(settings, httpx.MockTransport(redirect)))
    with pytest.raises(CollectionError, match="download_url_not_allowed"):
        collector.collect(CollectRequest(url=URL), checkpoint)
    assert len(seen) == 1


def test_cancellation_cannot_publish_blob(settings):
    def cancel():
        raise Cancelled("user_cancelled")

    with pytest.raises(Cancelled):
        fake_collector(settings, jar_bytes({"fabric.mod.json": fabric()})).collect(
            CollectRequest(url=URL), cancel
        )
    assert not list((settings.data_dir / "tmp").iterdir())
    assert not list((settings.data_dir / "blobs").iterdir())


def test_jarjar_library_is_evidence_not_external_mod_dependency(settings):
    payload = jar_bytes(
        {
            "fabric.mod.json": fabric(),
            "META-INF/jarjar/metadata.json": json.dumps(
                {"jars": [{"path": "META-INF/jarjar/library.jar"}]}
            ),
            "META-INF/jarjar/library.jar": jar_bytes({"library.txt": "library"}),
        }
    )
    result = fake_collector(settings, payload).collect(CollectRequest(url=URL), checkpoint)
    assert result.status == "complete_declared"
    assert len(result.mods) == 1
    assert any(row.member_path.endswith("library.jar") for row in result.evidence)


def test_concurrent_same_content_does_not_replace_in_use_blobs(settings):
    collector = fake_collector(settings, jar_bytes({"fabric.mod.json": fabric()}))

    def collect_one(_):
        return collector.collect(CollectRequest(url=URL), checkpoint)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect_one, range(4)))
    assert len({result.artifact_sha256 for result in results}) == 1
    assert all(result.status == "complete_declared" for result in results)
    assert len(list((settings.data_dir / "blobs").glob("*.jar"))) == 1
    assert not list((settings.data_dir / "tmp").iterdir())


def test_existing_corrupted_cas_content_is_never_trusted(settings):
    payload = jar_bytes({"fabric.mod.json": fabric()})
    collector = fake_collector(settings, payload)
    digest = hashlib.sha256(payload).hexdigest()
    (settings.data_dir / "blobs" / f"{digest}.jar").write_bytes(b"corrupted")
    with pytest.raises(CollectionError, match="content_store_corrupt"):
        collector.collect(CollectRequest(url=URL), checkpoint)


def test_missing_nested_jar_and_unresolved_version_are_partial(settings):
    result = fake_collector(
        settings, jar_bytes({"fabric.mod.json": fabric(jars=[{"file": "missing.jar"}])})
    ).collect(CollectRequest(url=URL), checkpoint)
    assert result.status == "partial"
    assert result.issues[0].code == "missing_nested_jar"
    raw = '[[mods]]\nmodId="actual"\nversion="${file.jarVersion}"\n'
    result = fake_collector(settings, jar_bytes({"META-INF/mods.toml": raw})).collect(
        CollectRequest(url=URL), checkpoint
    )
    assert result.status == "partial"
    assert result.mods[0].version_resolved is None


def test_nested_depth_and_total_extraction_budget(settings):
    inner = jar_bytes({"fabric.mod.json": fabric(id="inner")})
    payload = jar_bytes(
        {
            "fabric.mod.json": fabric(jars=[{"file": "inner.jar"}]),
            "inner.jar": inner,
        }
    )
    with pytest.raises(CollectionError, match="nested_jar_limit"):
        fake_collector(settings, payload, max_depth=0).collect(CollectRequest(url=URL), checkpoint)
    with pytest.raises(CollectionError, match="extraction_budget_exceeded"):
        fake_collector(settings, payload, max_extracted_bytes=10).collect(
            CollectRequest(url=URL), checkpoint
        )
