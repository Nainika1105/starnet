"""Photo storage behind a narrow interface.

STUB: Phase 1 writes to a local volume served by the API. The interface is the
seam for S3 / Supabase storage -- `save` and `url_for` are all a backend needs
to implement.

EXIF handling is not a stub: we read the capture time and GPS we need for the
geo coherence check, then write the image back stripped of everything else, so
we are not storing a photo that leaks the reporter's camera serial and full
location history.
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


def save(raw: bytes, *, suffix: str = ".jpg") -> str:
    """Persist the photo with EXIF stripped. Returns a storage key."""
    key = f"{uuid.uuid4().hex}{suffix}"
    target = _media_root() / key

    try:
        with Image.open(io.BytesIO(raw)) as img:
            clean = Image.new(img.mode, img.size)
            clean.putdata(list(img.getdata()))
            clean.save(target, format="JPEG" if suffix in (".jpg", ".jpeg") else None)
    except Exception:
        # Not a decodable image -- store the bytes as given rather than losing
        # the evidence. The geo check will score it accordingly.
        target.write_bytes(raw)

    return key


def url_for(key: str | None) -> str | None:
    return f"/media/{key}" if key else None
