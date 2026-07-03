from __future__ import annotations

import base64
import binascii
import re
from uuid import uuid4

from app.r2_storage import get_r2_service


APPLICATION_DOCUMENT_FIELDS = {
    "student_photo_data": "photo",
    "aadhar_card_data": "aadhar-card",
    "admission_receipt_data": "admission-receipt",
    "income_certificate_data": "income-certificate",
    "caste_certificate_data": "caste-certificate",
}

CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

DATA_URL_PATTERN = re.compile(r"^data:(?P<content_type>[-\w.]+/[-\w.+]+);base64,(?P<body>.+)$", re.DOTALL)


def safe_slug(value: str | int | None) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return text or "unknown"


def decode_data_url(value: str) -> tuple[bytes, str] | None:
    match = DATA_URL_PATTERN.match(value)
    if not match:
        return None
    content_type = match.group("content_type").lower()
    if content_type not in CONTENT_TYPE_EXTENSIONS:
        return None
    try:
        return base64.b64decode(match.group("body"), validate=True), content_type
    except (binascii.Error, ValueError):
        return None


def upload_application_documents(
    data: dict,
    *,
    student_id: int,
    application_id: int | None = None,
) -> dict:
    r2 = get_r2_service()
    if not r2.enabled:
        return data

    normalized = dict(data or {})
    application_part = f"application-{safe_slug(application_id)}" if application_id else "draft"
    for field, label in APPLICATION_DOCUMENT_FIELDS.items():
        value = normalized.get(field)
        if not isinstance(value, str) or not value.startswith("data:"):
            continue
        decoded = decode_data_url(value)
        if not decoded:
            continue
        body, content_type = decoded
        ext = CONTENT_TYPE_EXTENSIONS[content_type]
        key = (
            f"applications/student-{safe_slug(student_id)}/"
            f"{application_part}/{label}-{uuid4().hex}.{ext}"
        )
        normalized[field] = r2.upload_bytes(body, key, content_type=content_type)
    return normalized
