"""The S3 backend against a real HTTP S3 server.

test_storage.py asserts the contract with a stand-in client, which is the right
tool for "did we ask for the correct thing" but proves nothing about whether
boto3 can actually talk to an object store with the settings this app passes
it: a custom endpoint, path-style addressing, SigV4, and presigned URLs that
have to be fetchable by an ordinary HTTP client.

So this runs against a real S3 implementation over HTTP. It is moto rather than
Supabase or AWS, so it does not prove a particular provider's quirks -- but it
does exercise the whole code path end to end, which the fake cannot.

Skipped when moto is absent, so the suite still runs without the dev extras.
"""
from __future__ import annotations

import io
from fractions import Fraction

import httpx
import pytest

from PIL import Image

from app import storage
from app.engines.imagecheck import perceptual_hash

moto_server = pytest.importorskip(
    "moto.server", reason="needs the dev extras: pip install -r requirements-dev.txt"
)

BUCKET = "starnet-photos-test"


@pytest.fixture(scope="module")
def s3_server():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture
def s3_backend(s3_server, monkeypatch):
    for field, value in (
        ("storage_backend", "s3"),
        ("s3_bucket", BUCKET),
        ("s3_endpoint_url", s3_server),
        ("s3_access_key_id", "test"),
        ("s3_secret_access_key", "test"),
        ("s3_region", "us-east-1"),
    ):
        monkeypatch.setattr(storage.settings, field, value, raising=False)

    client = storage._s3_client()
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    return client


@pytest.fixture
def photo_with_exif() -> bytes:
    img = Image.new("RGB", (320, 240), (70, 90, 110))
    exif = Image.Exif()
    exif[36867] = "2026:11:03 10:00:00"
    # Fraction, not tuples: Pillow writes EXIF rationals through float() and
    # chokes on a (numerator, denominator) pair.
    exif[34853] = {
        1: "N",
        2: (Fraction(12), Fraction(58), Fraction(1884, 100)),
        3: "E",
        4: (Fraction(77), Fraction(35), Fraction(3733, 100)),
    }
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_a_photo_round_trips_through_object_storage(s3_backend, photo_with_exif):
    assert storage.read_photo_meta(photo_with_exif).captured_at is not None

    key = storage.save(photo_with_exif)
    url = storage.url_for(key)

    fetched = httpx.get(url, timeout=30)
    assert fetched.status_code == 200
    assert len(fetched.content) > 0

    # The privacy guarantee, asserted after a real round trip rather than on
    # the bytes we happened to hand the client.
    assert storage.read_photo_meta(fetched.content).captured_at is None
    assert not storage.read_photo_meta(fetched.content).has_gps

    # And it is still the same picture.
    assert perceptual_hash(fetched.content) == perceptual_hash(
        storage.strip_exif(photo_with_exif)
    )


def test_the_url_is_actually_signed(s3_backend, photo_with_exif):
    url = storage.url_for(storage.save(photo_with_exif))
    assert "X-Amz-Signature" in url
    assert "X-Amz-Expires" in url


def test_objects_are_not_world_readable(s3_backend, photo_with_exif):
    """The whole reason for presigning. These are photographs of identifiable
    places taken by identifiable people; guessing a key must not be enough."""
    key = storage.save(photo_with_exif)

    unsigned = httpx.get(f"{storage.settings.s3_endpoint_url}/{BUCKET}/{key}", timeout=30)
    assert unsigned.status_code in (401, 403), (
        f"an unsigned request returned {unsigned.status_code}; the object is public"
    )


def test_the_stored_object_carries_the_right_metadata(s3_backend, photo_with_exif):
    key = storage.save(photo_with_exif)
    head = s3_backend.head_object(Bucket=BUCKET, Key=key)

    assert head["ContentType"] == "image/jpeg"
    assert "private" in head.get("CacheControl", "")


def test_a_missing_key_does_not_raise_when_building_a_url(s3_backend):
    """Signing is local, so a URL for a key that was never uploaded is
    generated happily and 404s on fetch. Worth pinning: it means url_for is
    safe to call over a list of reports without a network round trip each."""
    url = storage.url_for("deadbeefdeadbeefdeadbeefdeadbeef.jpg")
    assert httpx.get(url, timeout=30).status_code == 404


def test_a_reported_photo_is_served_from_object_storage(
    db, client, register, s3_backend, photo_with_exif
):
    """The whole path, with object storage active: a report's photo URL must
    point at the store and be fetchable, not at this API's filesystem.

    Rehearsed by hand against a production-mode boot too, but pinned here so a
    change to `url_for` or the report serializer cannot quietly route photos
    back through local disk.
    """
    _, headers = register("prod_rehearsal")

    response = client.post(
        "/reports",
        headers=headers,
        data={"category": "POTHOLE", "lat": "12.9705", "lng": "77.5921"},
        files={"photo": ("live.jpg", photo_with_exif, "image/jpeg")},
    )
    assert response.status_code == 201, response.text

    url = response.json()["issue"]["photo_url"]
    assert url.startswith(storage.settings.s3_endpoint_url), url
    assert "X-Amz-Signature" in url

    fetched = httpx.get(url, timeout=30)
    assert fetched.status_code == 200
    assert storage.read_photo_meta(fetched.content).captured_at is None
