from __future__ import annotations

import base64
import logging
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import qrcode
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from xhtml2pdf import pisa

from app import models
from app.config import get_settings
from app.r2_storage import get_r2_service

logger = logging.getLogger(__name__)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = BACKEND_ROOT.parent
LOGO_CANDIDATES = [
    BACKEND_ROOT / "Magadh_Mahila_College.png",
    WORKSPACE_ROOT / "frontend" / "mmc-erp" / "Magadh_Mahila_College.png",
    WORKSPACE_ROOT / "Magadh_Mahila_College.png",
]
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "receipts"

templates = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

CATEGORY_NAMES = {
    "UR": "Unreserved",
    "BC": "Backward Class",
    "EBC": "Extremely Backward Class",
    "EWS": "Economically Weaker Section",
    "SC": "Scheduled Caste",
    "ST": "Scheduled Tribe",
}


def receipt_public_url(receipt: models.PaymentReceipt) -> str:
    return f"/receipts/{receipt.id}/download"


def receipt_dir() -> Path:
    return get_settings().receipt_dir_path


def receipt_pdf_path(receipt_number: str) -> Path:
    return receipt_dir() / f"{receipt_number}.pdf"


def verification_url(receipt_number: str) -> str:
    return f"{get_settings().base_url}/receipts/verify/{receipt_number}"


def safe(value: object | None) -> str:
    return "-" if value is None or value == "" else str(value)


def display_date(value: date | datetime | None, include_time: bool = False) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime) and include_time:
        return value.strftime("%d-%m-%Y %I:%M %p")
    return value.strftime("%d-%m-%Y")


def money(value: Decimal | int | float | None) -> str:
    amount = Decimal(value or 0)
    return f"Rs. {amount:,.2f}"


def money_plain(value: Decimal | int | float | None) -> str:
    amount = Decimal(value or 0)
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


def category(value: str | None) -> str:
    if not value:
        return "-"
    return f"{value} - {CATEGORY_NAMES.get(value, value)}"


def file_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def college_logo_path() -> Path | None:
    for path in LOGO_CANDIDATES:
        if path.exists():
            return path
    logger.warning("College logo not found in receipt logo candidates: %s", LOGO_CANDIDATES)
    return None


def college_logo_data_uri() -> str:
    path = college_logo_path()
    return file_data_uri(path) if path else ""


def college_logo_src() -> str:
    return "receipt-logo://college" if college_logo_path() else college_logo_data_uri()


def qr_data_uri(value: str) -> str:
    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def generate_receipt_number(db: Session, receipt_type: str) -> str:
    prefix = "HAP" if receipt_type == "hostel_admission" else "OHA"
    year = datetime.now().year
    receipt_numbers = db.scalars(
        select(models.PaymentReceipt.receipt_number).where(
            models.PaymentReceipt.receipt_number.like(f"{prefix}{year}%")
        )
    )
    max_sequence = 0
    base_length = len(prefix) + 4
    for receipt_number in receipt_numbers:
        suffix = str(receipt_number or "")[base_length:]
        if suffix.isdigit():
            max_sequence = max(max_sequence, int(suffix))
    return f"{prefix}{year}{max_sequence + 1:06d}"


def infer_receipt_type(payment: models.Payment) -> str:
    payment_type = (payment.payment_type or "").lower()
    amount = Decimal(payment.amount or 0)
    if "hostel" in payment_type or amount in {Decimal("12000"), Decimal("10000")}:
        return "hostel_admission"
    return "application_registration"


def common_context(receipt: models.PaymentReceipt, payment: models.Payment) -> dict:
    app = payment.application
    paid_at = payment.paid_at or payment.created_at
    return {
        "receipt_number": receipt.receipt_number,
        "generated_at": display_date(receipt.generated_at or datetime.now(), include_time=True),
        "payment_date_only": display_date(paid_at),
        "logo_src": college_logo_src(),
        "logo_data": college_logo_data_uri(),
        "photo_data": app.student_photo_data if app and app.student_photo_data else "",
        "qr_data": qr_data_uri(verification_url(receipt.receipt_number)),
    }


def registration_context(receipt: models.PaymentReceipt, payment: models.Payment) -> dict:
    student = payment.student
    app = payment.application
    context = common_context(receipt, payment)
    context.update({
        "applicant_fields": [
            ("Receipt No.", safe(receipt.receipt_number)),
            ("Application No.", safe(app.application_no if app else None)),
            ("Applicant's Name", safe(student.name if student else None)),
            ("Gender", safe(student.gender if student else None)),
            ("DOB (dd-mm-yyyy)", display_date(student.date_of_birth if student else None)),
            ("Mobile No.", safe(student.mobile if student else None)),
            ("Email ID", safe(student.email if student else None)),
            ("Father's Name", safe(app.father_name if app else None)),
            ("Mother's Name", safe(app.mother_name if app else None)),
            ("Local Guardian's Name", safe(app.guardian_name if app else None)),
            ("Local Guardian's Mobile No.", safe(app.guardian_mobile if app else None)),
            ("Blood Group", safe(app.blood_group if app else None)),
            ("Aadhaar No.", safe(app.aadhar_number if app else None)),
            ("Category", category(app.applied_category if app else student.category if student else None)),
            ("Religion", safe(app.religion if app else None)),
            ("Nationality", safe(app.nationality if app else None)),
            ("Correspondence Address", safe(app.correspondence_address if app else None)),
        ],
        "academic_fields": [
            ("Intermediate College Name", safe(app.intermediate_college if app else None)),
            ("Intermediate Board", safe(app.board if app else None)),
            ("Intermediate Total Marks", safe(app.total_marks if app else None)),
            ("Intermediate Marks Obtained", safe(app.marks_obtained if app else None)),
            ("Intermediate Result Type", safe(app.result_type if app else None)),
            ("Intermediate Aggregate Percentage", f"{safe(app.percentage if app else None)}%"),
            ("Admission ID", safe(app.admission_id if app else None)),
            ("College Name", safe(app.college_name if app else None)),
            ("Course Name", safe(app.course if app else student.course if student else None)),
            ("Honours Subject", safe(app.subject if app else None)),
            ("Session", safe(app.session if app else student.session if student else None)),
            ("Roll Number", safe(app.roll_number if app else None)),
        ],
        "payment_fields": [
            ("Payment Amount (Rs.)", money_plain(payment.amount)),
            ("Payment Status", safe(payment.status).upper()),
            ("Transaction ID", safe(payment.transaction_no)),
            ("Payment Date", display_date(payment.paid_at or payment.created_at, include_time=True)),
            ("Payment Mode", safe(payment.mode)),
        ],
    })
    return context


def hostel_context(receipt: models.PaymentReceipt, payment: models.Payment) -> dict:
    student = payment.student
    app = payment.application
    hostel = app.hostel if app else None
    room = app.room if app else None
    context = common_context(receipt, payment)
    context.update({
        "student_fields": [
            ("Receipt No.", safe(receipt.receipt_number)),
            ("Application No.", safe(app.application_no if app else None)),
            ("Admission ID", safe(app.admission_id if app else None)),
            ("Applicant's Name", safe(student.name if student else None)),
            ("Gender", safe(student.gender if student else None)),
            ("Mobile No.", safe(student.mobile if student else None)),
            ("Email ID", safe(student.email if student else None)),
            ("Hostel Name", safe(hostel.name if hostel else receipt.hostel_name)),
            ("Room No.", safe(room.room_number if room else receipt.room_number)),
            ("Bed No.", safe(app.bed if app else None)),
            ("Course", safe(app.course if app else student.course if student else None)),
            ("Session", safe(app.session if app else student.session if student else None)),
        ],
        "hostel_fields": [
            ("Payment Amount (Rs.)", money_plain(payment.amount)),
            ("Payment Status", safe(payment.status).upper()),
            ("Transaction ID", safe(payment.transaction_no)),
            ("Payment Date", display_date(payment.paid_at or payment.created_at, include_time=True)),
            ("Payment Mode", safe(payment.mode)),
        ],
    })
    return context


def receipt_link_callback(uri: str, rel: str) -> str:
    if uri == "receipt-logo://college":
        path = college_logo_path()
        if path:
            return str(path)
    return uri


def render_html_pdf_to_bytes(template_name: str, context: dict) -> bytes:
    """Render an HTML template to PDF bytes in memory."""
    html = templates.get_template(template_name).render(**context)
    buffer = BytesIO()
    result = pisa.CreatePDF(
        html,
        dest=buffer,
        encoding="utf-8",
        link_callback=receipt_link_callback,
    )
    if result.err:
        raise RuntimeError(f"Receipt PDF rendering failed with {result.err} error(s).")
    return buffer.getvalue()


def render_html_pdf(template_name: str, context: dict, path: Path) -> None:
    """Render an HTML template to PDF and save to disk (legacy fallback)."""
    pdf_bytes = render_html_pdf_to_bytes(template_name, context)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)


def build_receipt_pdf_bytes(
    receipt: models.PaymentReceipt,
    payment: models.Payment,
    receipt_type: str | None = None,
) -> bytes:
    receipt_type = receipt_type or receipt.receipt_type or infer_receipt_type(payment)
    template_name = (
        "hostel_payment_receipt.html" if receipt_type == "hostel_admission"
        else "registration_receipt.html"
    )
    context = (
        hostel_context(receipt, payment) if receipt_type == "hostel_admission"
        else registration_context(receipt, payment)
    )
    return render_html_pdf_to_bytes(template_name, context)


def upload_receipt_to_r2(receipt_number: str, pdf_bytes: bytes) -> str:
    """Upload receipt PDF to Cloudflare R2 and return the public URL."""
    r2 = get_r2_service()
    if not r2.enabled:
        return ""
    key = r2.receipt_key(receipt_number)
    try:
        url = r2.upload_bytes(pdf_bytes, key, content_type="application/pdf")
        logger.info("Receipt %s uploaded to R2: %s", receipt_number, url)
        return url
    except Exception:
        logger.exception("Failed to upload receipt %s to R2", receipt_number)
        return ""


def get_receipt_pdf_bytes(receipt_number: str) -> bytes | None:
    """Try to get receipt PDF bytes — from R2 first, then local disk."""
    r2 = get_r2_service()
    if r2.enabled:
        key = r2.receipt_key(receipt_number)
        try:
            return r2.download_file(key)
        except FileNotFoundError:
            logger.debug("Receipt %s not found in R2, checking local.", receipt_number)
        except Exception:
            logger.exception("Failed to download receipt %s from R2", receipt_number)

    local_path = receipt_pdf_path(receipt_number)
    if local_path.exists():
        return local_path.read_bytes()
    return None


def generate_receipt_pdf(
    db: Session,
    payment: models.Payment,
    receipt_type: str | None = None,
) -> models.PaymentReceipt:
    receipt_type = receipt_type or infer_receipt_type(payment)
    existing = db.query(models.PaymentReceipt).filter(
        models.PaymentReceipt.payment_id == payment.id,
        models.PaymentReceipt.receipt_type == receipt_type,
    ).one_or_none()
    receipt = existing or models.PaymentReceipt(
        receipt_number=generate_receipt_number(db, receipt_type),
        application_number=payment.application.application_no if payment.application else None,
        student_id=payment.student_id,
        receipt_type=receipt_type,
        payment_id=payment.id,
        hostel_name=payment.application.hostel.name if payment.application and payment.application.hostel else None,
        room_number=payment.application.room.room_number if payment.application and payment.application.room else None,
        amount=payment.amount,
        transaction_id=payment.transaction_no,
    )
    if not existing:
        db.add(receipt)
        db.flush()

    receipt.qr_code = verification_url(receipt.receipt_number)

    pdf_bytes = build_receipt_pdf_bytes(receipt, payment, receipt_type)

    local_path = receipt_pdf_path(receipt.receipt_number)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(pdf_bytes)

    # Upload to R2 if available
    r2_url = upload_receipt_to_r2(receipt.receipt_number, pdf_bytes)
    if r2_url:
        receipt.pdf_url = r2_url
    else:
        receipt.pdf_url = receipt_public_url(receipt)

    db.commit()
    db.refresh(receipt)
    return receipt


def ensure_receipts_for_successful_payments(db: Session, student_id: int | None = None) -> int:
    successful_statuses = ["Paid", "Success", "Completed", "paid", "success", "completed"]
    stmt = (
        select(models.Payment)
        .options(
            joinedload(models.Payment.student),
            joinedload(models.Payment.application).joinedload(models.HostelApplication.hostel),
            joinedload(models.Payment.application).joinedload(models.HostelApplication.room),
            selectinload(models.Payment.receipts),
        )
        .where(models.Payment.status.in_(successful_statuses))
        .order_by(models.Payment.created_at.desc())
    )
    if student_id:
        stmt = stmt.where(models.Payment.student_id == student_id)
    generated = 0
    for payment in db.scalars(stmt):
        receipt_type = infer_receipt_type(payment)
        if any(receipt.receipt_type == receipt_type for receipt in payment.receipts):
            continue
        try:
            generate_receipt_pdf(db, payment, receipt_type)
            generated += 1
        except Exception:
            payment_id = payment.id
            db.rollback()
            logger.exception("Could not auto-generate missing receipt for payment %s.", payment_id)
    return generated
