"""Photo storage: the privacy guarantee, and the two backends.

The privacy claim is load-bearing and testable, so it is tested: what gets
stored must carry none of the EXIF that was read off the upload. The S3 path is
exercised against a stand-in client -- these assert the contract the module
honours (private objects, presigned reads, stripped bytes), not that AWS works.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import storage
from app.config import Settings


@pytest.fixture
def with_exif(photo_bytes):
    return photo_bytes(captured_at=datetime.now(timezone.utc), lat=12.9719, lng=77.5937)


# --- the privacy guarantee -------------------------------------------------


def test_the_upload_carries_exif_that_the_stored_copy_does_not(with_exif):
    """Read the metadata, keep the signal, store none of it."""
    original = storage.read_photo_meta(with_exif)
    assert original.captured_at is not None and original.has_gps

    stored = storage.read_photo_meta(storage.strip_exif(with_exif))
    assert stored.captured_at is None
    assert not stored.has_gps


def test_stripping_keeps_the_picture(with_exif):
    from PIL import Image
    import io

    before = Image.open(io.BytesIO(with_exif))
    after = Image.open(io.BytesIO(storage.strip_exif(with_exif)))
    assert after.size == before.size
    assert after.mode == before.mode


def test_undecodable_bytes_survive_rather_than_being_dropped():
    """A corrupt upload is still what somebody submitted."""
    junk = b"not an image at all"
    assert storage.strip_exif(junk) == junk


def test_saving_locally_writes_the_stripped_copy(with_exif, tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local", raising=False)
    monkeypatch.setattr(storage.settings, "media_root", str(tmp_path), raising=False)

    key = storage.save(with_exif)
    written = (tmp_path / key).read_bytes()

    assert storage.read_photo_meta(written).captured_at is None
    assert storage.url_for(key) == f"/media/{key}"
    assert storage.url_for(None) is None


# --- the s3 backend --------------------------------------------------------


class FakeS3:
    """Records what it was asked to do, so the contract can be asserted."""

    def __init__(self):
        self.puts: list[dict] = []
        self.presigned: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        self.presigned.append({"op": operation, "params": Params, "ttl": ExpiresIn})
        return f"https://store.example.org/{Params['Key']}?signed=yes&expires={ExpiresIn}"


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(storage.settings, "storage_backend", "s3", raising=False)
    monkeypatch.setattr(storage.settings, "s3_bucket", "starnet-photos", raising=False)
    monkeypatch.setattr(storage.settings, "s3_url_ttl_seconds", 1800, raising=False)
    monkeypatch.setattr(storage, "_s3_client", lambda: fake)
    return fake


def test_s3_save_uploads_the_stripped_bytes_privately(s3, with_exif):
    key = storage.save(with_exif)

    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == "starnet-photos"
    assert put["Key"] == key
    assert put["ContentType"] == "image/jpeg"
    # No public-read ACL anywhere near this.
    assert "ACL" not in put
    assert "private" in put["CacheControl"]
    # And the bytes that left the process carry no metadata.
    assert storage.read_photo_meta(put["Body"]).captured_at is None


def test_s3_urls_are_presigned_and_expire(s3):
    url = storage.url_for("abc123.jpg")

    assert s3.presigned[0]["op"] == "get_object"
    assert s3.presigned[0]["params"] == {"Bucket": "starnet-photos", "Key": "abc123.jpg"}
    assert s3.presigned[0]["ttl"] == 1800
    assert url.startswith("https://store.example.org/abc123.jpg")


def test_s3_backend_still_returns_none_for_no_photo(s3):
    assert storage.url_for(None) is None
    assert s3.presigned == []


def test_keys_are_unique_and_carry_no_user_data(s3, with_exif):
    """A key must not encode who uploaded it or where they were."""
    keys = {storage.save(with_exif) for _ in range(5)}
    assert len(keys) == 5
    for key in keys:
        assert key.endswith(".jpg")
        assert len(key) == len("0123456789abcdef0123456789abcdef") + len(".jpg")


def test_the_database_only_ever_holds_the_key(s3, with_exif):
    """Which is why switching backends needs no data migration."""
    key = storage.save(with_exif)
    assert "://" not in key and "/" not in key


# --- misconfiguration ------------------------------------------------------


def test_missing_s3_credentials_name_themselves(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "s3", raising=False)
    for field in ("s3_bucket", "s3_access_key_id", "s3_secret_access_key"):
        monkeypatch.setattr(storage.settings, field, "", raising=False)

    with pytest.raises(RuntimeError) as exc:
        storage._s3_client()

    message = str(exc.value)
    for expected in ("STARNET_S3_BUCKET", "STARNET_S3_ACCESS_KEY_ID", "STARNET_S3_SECRET_ACCESS_KEY"):
        assert expected in message


def test_an_unknown_backend_name_falls_back_to_local(with_exif, tmp_path, monkeypatch):
    """A typo in the backend name must not silently drop uploads."""
    monkeypatch.setattr(storage.settings, "storage_backend", "S3-ish-typo", raising=False)
    monkeypatch.setattr(storage.settings, "media_root", str(tmp_path), raising=False)

    key = storage.save(with_exif)
    assert (tmp_path / key).exists()


def test_the_backend_name_is_case_and_space_insensitive(s3, with_exif, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", " S3 ", raising=False)
    storage.save(with_exif)
    assert len(s3.puts) == 1


def test_production_rejects_ephemeral_local_storage():
    """The guard treats losing the photos as a misconfiguration, not a choice."""
    base = dict(
        environment="production", jwt_secret="x" * 48, cors_origins="https://a.example.org"
    )
    assert any("STORAGE_BACKEND" in p for p in Settings(**base).production_problems())
    assert Settings(**base, storage_backend="s3").production_problems() == []
