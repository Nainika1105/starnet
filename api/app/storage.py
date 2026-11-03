"""Photo storage behind a narrow interface: `save` and `url_for`.

Two backends, chosen by STARNET_STORAGE_BACKEND:

  local  A directory the API serves at /media. Fine for development, and wrong
         for most deployments: a container filesystem is ephemeral, so the
         photos -- which ARE the evidence this whole mechanism rests on -- are
         lost on the next redeploy.
  db     Rows in Postgres, served back through /media. For a host with an
         ephemeral disk and no object storage -- a free tier, typically --
         where the alternative is a demo whose every image 404s after a
         deploy. Durable, and deliberately not the answer at scale: blobs in
         the primary database bloat backups and compete with queries for cache.
  s3     Any S3-compatible object store: AWS, Supabase Storage, Cloudflare R2,
         MinIO. Objects are written private and handed out as presigned URLs,
         so a photo is not world-readable just because someone guessed a key.

The split matters more here than in a typical app. A reward that vests on a
photographed repair is only auditable for as long as the photographs exist.

EXIF handling is not a stub: we read the capture time and GPS the geo-coherence
check needs, then write the image back stripped of everything else, so we are
not storing a photo that leaks the reporter's camera serial and full location
history. That happens before upload, so the stripped image is the only one that
ever leaves the process.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ExifTags

from app.config import settings

_EXIF_DATETIME = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")
_EXIF_GPS_IFD = next(k for k, v in ExifTags.TAGS.items() if v == "GPSInfo")

# GPSInfo sub-tags, by name, so the lookups below read as themselves.
_GPS = {v: k for k, v in ExifTags.GPSTAGS.items()}


@dataclass(frozen=True)
class PhotoMeta:
    """The only EXIF we keep: what the geo-coherence check needs.

    Everything else -- camera serial, lens, orientation history, thumbnails --
    is dropped by `save`, so the stored image cannot be used to fingerprint the
    reporter's device or reconstruct where else they have been.
    """

    captured_at: datetime | None = None
    lat: float | None = None
    lng: float | None = None

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lng is not None


def _media_root() -> Path:
    root = Path(settings.media_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ratio(value) -> float:
    """EXIF rationals arrive as Fraction-likes, ints, or (num, den) tuples."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        num, den = value
        return float(num) / float(den) if den else 0.0
    return float(value)


def _dms_to_degrees(dms, ref: str | None) -> float | None:
    """EXIF stores coordinates as degrees/minutes/seconds plus a hemisphere."""
    try:
        degrees, minutes, seconds = (_ratio(v) for v in dms)
    except (TypeError, ValueError):
        return None

    value = degrees + minutes / 60.0 + seconds / 3600.0
    if ref and str(ref).strip().upper() in ("S", "W"):
        value = -value
    return value


def read_photo_meta(raw: bytes) -> PhotoMeta:
    """Capture time and GPS from EXIF, or empty fields when absent.

    Never raises: a photo with no EXIF, mangled EXIF, or that is not a decodable
    image at all is a legitimate (if weaker) submission, and the geo score is
    what decides how much it is worth -- not an exception here.
    """
    try:
        with Image.open(io.BytesIO(raw)) as img:
            exif = img.getexif()
            if not exif:
                return PhotoMeta()

            captured_at = None
            value = exif.get(_EXIF_DATETIME)
            if value:
                try:
                    captured_at = datetime.strptime(
                        str(value), "%Y:%m:%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    captured_at = None

            lat = lng = None
            try:
                gps = exif.get_ifd(_EXIF_GPS_IFD) or {}
            except Exception:
                gps = {}
            if gps:
                lat = _dms_to_degrees(
                    gps.get(_GPS["GPSLatitude"]), gps.get(_GPS["GPSLatitudeRef"])
                )
                lng = _dms_to_degrees(
                    gps.get(_GPS["GPSLongitude"]), gps.get(_GPS["GPSLongitudeRef"])
                )
                # (0, 0) is what a camera writes when it has no fix, not a
                # report from the Gulf of Guinea.
                if lat == 0.0 and lng == 0.0:
                    lat = lng = None

            return PhotoMeta(captured_at=captured_at, lat=lat, lng=lng)
    except Exception:
        return PhotoMeta()


def read_exif_time(raw: bytes) -> datetime | None:
    """Kept for callers that only want the timestamp."""
    return read_photo_meta(raw).captured_at


def strip_exif(raw: bytes, *, suffix: str = ".jpg") -> bytes:
    """Re-encode the image with no EXIF at all.

    Done by copying the pixels into a fresh image rather than by deleting tags,
    because `getexif()` does not see every place a camera can hide metadata --
    maker notes, XMP packets and embedded thumbnails all survive a tag delete.
    Copying the pixels leaves nothing behind by construction.

    Undecodable bytes are returned unchanged: a corrupt upload is still the
    evidence someone submitted, and the geo score already accounts for it.
    """
    try:
        with Image.open(io.BytesIO(raw)) as img:
            clean = Image.new(img.mode, img.size)
            clean.putdata(list(img.getdata()))
            buf = io.BytesIO()
            clean.save(buf, format="JPEG" if suffix in (".jpg", ".jpeg") else None)
            return buf.getvalue()
    except Exception:
        return raw


def _backend() -> str:
    return settings.storage_backend.strip().lower()


# --- s3-compatible ---------------------------------------------------------


def _s3_client():
    """Lazily built, so a local deployment never needs boto3 installed."""
    try:
        import boto3
        from botocore.config import Config
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "STARNET_STORAGE_BACKEND=s3 needs boto3. Install it with "
            "`pip install -r requirements.txt`."
        ) from exc

    missing = [
        name
        for name, value in (
            ("STARNET_S3_BUCKET", settings.s3_bucket),
            ("STARNET_S3_ACCESS_KEY_ID", settings.s3_access_key_id),
            ("STARNET_S3_SECRET_ACCESS_KEY", settings.s3_secret_access_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "STARNET_STORAGE_BACKEND=s3 but these are unset: " + ", ".join(missing)
        )

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        # Supabase Storage, R2 and MinIO all want path-style addressing;
        # virtual-host style assumes a bucket subdomain they do not serve.
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def _save_to_db(key: str, clean: bytes) -> None:
    """Insert the photo as a row.

    Opens its own session rather than taking the request's. Storage is called
    from the middle of report intake, and a failure to write a photo should
    not poison the transaction that is recording the report itself.
    """
    from app.db import SessionLocal
    from app.models import MediaObject

    with SessionLocal() as db:
        db.add(MediaObject(key=key, content_type="image/jpeg", data=clean))
        db.commit()


def load(key: str) -> tuple[bytes, str] | None:
    """Read a photo back. Only the `db` backend needs this.

    `local` is served by a static mount and `s3` by a presigned URL straight
    from the store, so neither passes bytes back through this process.
    """
    if _backend() != "db":
        return None

    from app.db import SessionLocal
    from app.models import MediaObject

    with SessionLocal() as db:
        row = db.get(MediaObject, key)
        if row is None:
            return None
        return bytes(row.data), row.content_type


def save(raw: bytes, *, suffix: str = ".jpg") -> str:
    """Persist the photo with EXIF stripped. Returns a storage key.

    The key is the only thing the database holds. Nothing about where the bytes
    live leaks into the schema, so switching backends does not require a data
    migration -- only moving the objects.
    """
    key = f"{uuid.uuid4().hex}{suffix}"
    clean = strip_exif(raw, suffix=suffix)

    if _backend() == "db":
        _save_to_db(key, clean)
        return key

    if _backend() == "s3":
        _s3_client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=clean,
            ContentType="image/jpeg",
            # No public-read ACL: these are photographs of identifiable places
            # taken by identifiable people, handed out through presigned URLs
            # rather than left readable to anyone who guesses a key.
            CacheControl="private, max-age=31536000",
        )
        return key

    (_media_root() / key).write_bytes(clean)
    return key


def url_for(key: str | None) -> str | None:
    """A URL the dashboard or app can load the photo from.

    For s3 this is a presigned GET, signed locally with no network call, so
    generating one per report in a list response costs nothing. It expires,
    which is the point: a URL that leaks stops working.
    """
    if not key:
        return None

    if _backend() == "s3":
        return _s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=settings.s3_url_ttl_seconds,
        )

    # Both `local` and `db` are served by this app under /media -- a static
    # mount for one, a route for the other -- so the URL is the same either way
    # and nothing downstream has to know which is in use.
    return f"/media/{key}"
