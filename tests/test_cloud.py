import base64
import hashlib
import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import boto3
import pytest
from botocore.stub import ANY, Stubber
from conftest import fabric, fake_collector, jar_bytes
from sqlalchemy import update
from sqlalchemy.orm import Session

from bugg_source.archive_worker import ArchiveWorker
from bugg_source.cloud import CloudProfile, S3Archive
from bugg_source.contracts import CollectionError, CollectRequest
from bugg_source.database import ArchiveObject, ArchiveSpace, UploadBudget
from bugg_source.repository import Repository, utcnow
from bugg_source.worker import Worker


@pytest.fixture
def cloud_settings(settings, tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "qiniu_profiles": [
                    {
                        "id": "rainyun-test",
                        "provider": "rainyun",
                        "endpoint": "https://example.invalid",
                        "region": "cn-nb1",
                        "bucket": "test-bucket",
                        "prefix": "mirror",
                        "quota_gib": 1,
                        "daily_upload_gib": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return replace(settings, cloud_profile_file=path, cloud_profile_id="rainyun-test")


@pytest.mark.parametrize("mode", ["upload", "exists", "mismatch", "local_corrupt"])
def test_s3_checks_actual_local_and_server_checksum(cloud_settings, tmp_path, mode):
    profile = CloudProfile.load(cloud_settings)
    payload = b"archive bytes"
    path = tmp_path / "file.jar"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    checksum = base64.b64encode(bytes.fromhex(digest)).decode()
    key = profile.object_prefix + "/blobs/" + digest + ".jar"
    client = boto3.client(
        "s3",
        region_name="cn-nb1",
        endpoint_url="https://example.invalid",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    head = {"Bucket": profile.bucket, "Key": key, "ChecksumMode": "ENABLED"}
    response = {"ContentLength": len(payload), "ChecksumSHA256": checksum}
    with Stubber(client) as stub:
        if mode == "upload":
            stub.add_client_error(
                "head_object", service_error_code="404", http_status_code=404, expected_params=head
            )
            stub.add_response(
                "put_object",
                {},
                {
                    "Bucket": profile.bucket,
                    "Key": key,
                    "Body": ANY,
                    "ContentLength": len(payload),
                    "ChecksumSHA256": checksum,
                    "ContentType": "application/octet-stream",
                    "Metadata": {"sha256": digest},
                },
            )
            stub.add_response("head_object", response, head)
        elif mode == "exists":
            stub.add_response("head_object", response, head)
        elif mode == "mismatch":
            # User metadata is insufficient proof, even with the expected SHA text.
            stub.add_response(
                "head_object", {"ContentLength": len(payload), "Metadata": {"sha256": digest}}, head
            )
        else:
            path.write_bytes(b"changed")
        archive = S3Archive(cloud_settings, profile, client=client)
        if mode in ("mismatch", "local_corrupt"):
            with pytest.raises(
                CollectionError, match="cloud_checksum_mismatch|archive_local_hash_mismatch"
            ):
                archive.put_verified(path, key, digest, len(payload), lambda: None)
        else:
            archive.put_verified(path, key, digest, len(payload), lambda: None)
        stub.assert_no_pending_responses()
    client.close()


def test_cloud_staging_dedup_backfill_and_worker_failure_keeps_facts(cloud_settings, repository):
    payload = jar_bytes({"fabric.mod.json": fabric()})
    collector = fake_collector(cloud_settings, payload)
    # First collect without cloud; backfill is explicit and bounded.
    request = CollectRequest(url="https://cdn.modrinth.com/test.jar")
    identifier = repository.enqueue(request)
    Worker(cloud_settings, repository, collector).run_once()
    repo = Repository(repository.engine, settings=cloud_settings)
    assert repo.backfill_archives(1) == 1
    assert repo.backfill_archives(1) == 0
    digest = repo.get_job(identifier)["artifact_sha256"]
    repo.save_local_scan(
        collector.import_local(cloud_settings.data_dir / "blobs" / f"{digest}.jar", lambda: None)
    )
    assert repo.status()["cloud"]["states"] == {"queued": 2}
    failed = Mock()
    failed.put_verified.side_effect = CollectionError("cloud_transfer_failed")
    worker = ArchiveWorker(cloud_settings, repo, failed)
    assert worker.run_once()
    assert repo.status()["cloud"]["states"]["failed"] == 1
    assert repo.get_scan(digest, "1.0.0")["mods"][0]["mod_id"] == "actual_mod"
    assert (cloud_settings.data_dir / "blobs" / f"{digest}.jar").is_file()
    assert repo.retry_archives(1) == 1
    failed.put_verified.side_effect = None
    assert worker.run_once() and worker.run_once() and not worker.run_once()
    assert repo.status()["cloud"]["states"] == {"succeeded": 2}
    status = repo.status()
    assert status["cloud"]["reserved_capacity_bytes"] == len(payload) + len(fabric().encode())
    assert "access_key" not in json.dumps(status)


def test_archive_lease_and_capacity_budget(cloud_settings, repository):
    repo = Repository(repository.engine, settings=cloud_settings)
    result = fake_collector(cloud_settings, jar_bytes({"fabric.mod.json": fabric()})).collect(
        CollectRequest(url="https://cdn.modrinth.com/test.jar"), lambda: None
    )
    repo.save_local_scan(result)
    first = repo.claim_archive()
    with Session(repo.engine) as session, session.begin():
        session.execute(
            update(ArchiveObject)
            .where(ArchiveObject.object_key == first["key"])
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
    second = repo.claim_archive()
    assert first["key"] == second["key"] and first["token"] != second["token"]
    assert not repo.finish_archive(first["key"], first["token"])
    assert repo.finish_archive(second["key"], second["token"])
    with Session(repo.engine) as session, session.begin():
        session.execute(update(ArchiveSpace).values(reserved_bytes=repo.cloud.capacity_bytes))
    assert repo.claim_archive() is None
    assert repo.status()["cloud"]["states"]["failed"] == 1


def test_upload_budget_is_conservative_and_not_refunded(cloud_settings, repository):
    repo = Repository(repository.engine, settings=cloud_settings)
    result = fake_collector(cloud_settings, jar_bytes({"fabric.mod.json": fabric()})).collect(
        CollectRequest(url="https://cdn.modrinth.com/test.jar"), lambda: None
    )
    repo.save_local_scan(result)
    item = repo.claim_archive()
    assert repo.finish_archive(item["key"], item["token"], error="upload_failed")
    spent = repo.status()["cloud"]["reserved_upload_bytes"]
    assert spent == item["size"] * 3
    with Session(repo.engine) as session, session.begin():
        session.execute(update(UploadBudget).values(reserved_bytes=1024**3))
    assert repo.claim_archive() is None
    assert repo.status()["cloud"]["states"]["failed"] == 2


def test_cloud_requires_explicit_new_configuration(settings, cloud_settings):
    assert CloudProfile.load(settings) is None
    with pytest.raises(CollectionError, match="cloud_credentials_unavailable"):
        S3Archive(cloud_settings, CloudProfile.load(cloud_settings))
