from __future__ import annotations

import base64
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import qrcode
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from xhtml2pdf import pisa

from app import models
from app.config import get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGO_PATH = PROJECT_ROOT / "Magadh_Mahila_College.png"
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


def category(value: str | None) -> str:
    if not value:
        return "-"
    return f"{value} - {CATEGORY_NAMES.get(value, value)}"


def file_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def qr_data_uri(value: str) -> str:
    image = qrcode.make(value)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"


def generate_receipt_number(db: Session, receipt_type: str) -> str:
    prefix = "HAP" if receipt_type == "hostel_admission" else "OHA"
    year = datetime.now().year
    count = db.query(models.PaymentReceipt).filter(
        models.PaymentReceipt.receipt_number.like(f"{prefix}{year}%")
    ).count()
    return f"{prefix}{year}{count + 1:06d}"


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
        "logo_data": file_data_uri(LOGO_PATH),
        "photo_data": app.student_photo_data if app and app.student_photo_data else "",
        "qr_data": qr_data_uri(verification_url(receipt.receipt_number)),
    }


def registration_context(receipt: models.PaymentReceipt, payment: models.Payment) -> dict:
    student = payment.student
    app = payment.application
    context = common_context(receipt, payment)
    context.update({
        "applicant_fields": [
            ("Application Number", safe(app.application_no if app else None)),
            ("Applicant Name", safe(student.name if student else None)),
            ("Gender", safe(student.gender if student else None)),
            ("Date of Birth", display_date(student.date_of_birth if student else None)),
            ("Mobile Number", safe(student.mobile if student else None)),
            ("Email", safe(student.email if student else None)),
            ("Father Name", safe(app.father_name if app else None)),
            ("Mother Name", safe(app.mother_name if app else None)),
            ("Local Guardian Name", safe(app.guardian_name if app else None)),
            ("Guardian Mobile", safe(app.guardian_mobile if app else None)),
            ("Blood Group", safe(app.blood_group if app else None)),
            ("Aadhar Number", safe(app.aadhar_number if app else None)),
            ("Applied Category", category(app.applied_category if app else student.category if student else None)),
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
            ("Registration Fee", money(payment.amount)),
            ("Transaction ID", safe(payment.transaction_no)),
            ("Payment Status", safe(payment.status)),
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
            ("Application Number", safe(app.application_no if app else None)),
            ("Admission ID", safe(app.admission_id if app else None)),
            ("Student Name", safe(student.name if student else None)),
            ("Gender", safe(student.gender if student else None)),
            ("Date of Birth", display_date(student.date_of_birth if student else None)),
            ("Mobile Number", safe(student.mobile if student else None)),
            ("Email", safe(student.email if student else None)),
            ("Father Name", safe(app.father_name if app else None)),
            ("Mother Name", safe(app.mother_name if app else None)),
            ("Local Guardian Name", safe(app.guardian_name if app else None)),
            ("Guardian Mobile", safe(app.guardian_mobile if app else None)),
            ("Blood Group", safe(app.blood_group if app else None)),
            ("Aadhar Number", safe(app.aadhar_number if app else None)),
            ("Applied Category", category(app.applied_category if app else None)),
            ("Allotted Category", category(app.allotted_category if app else None)),
            ("Religion", safe(app.religion if app else None)),
            ("Nationality", safe(app.nationality if app else None)),
            ("Correspondence Address", safe(app.correspondence_address if app else None)),
        ],
        "admission_fields": [
            ("Admission ID", safe(app.admission_id if app else None)),
            ("College Name", safe(app.college_name if app else None)),
            ("Course Name", safe(app.course if app else student.course if student else None)),
            ("Honours Subject", safe(app.subject if app else None)),
            ("Session", safe(app.session if app else student.session if student else None)),
            ("Roll Number", safe(app.roll_number if app else None)),
        ],
        "hostel_fields": [
            ("Hostel Name", safe(hostel.name if hostel else receipt.hostel_name)),
            ("Room Number", safe(room.room_number if room else receipt.room_number)),
            ("Bed Number", "-"),
            ("Payment Amount", money(payment.amount)),
            ("Transaction ID", safe(payment.transaction_no)),
            ("Payment Date", display_date(payment.paid_at or payment.created_at, include_time=True)),
            ("Payment Mode", safe(payment.mode)),
            ("Payment Status", safe(payment.status)),
        ],
    })
    return context


def render_html_pdf(template_name: str, context: dict, path: Path) -> None:
    html = templates.get_template(template_name).render(**context)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        result = pisa.CreatePDF(html, dest=output, encoding="utf-8")
    if result.err:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Receipt PDF rendering failed with {result.err} error(s).")


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
    receipt.pdf_url = receipt_public_url(receipt)
    path = receipt_pdf_path(receipt.receipt_number)
    if receipt_type == "hostel_admission":
        render_html_pdf("hostel_payment_receipt.html", hostel_context(receipt, payment), path)
    else:
        render_html_pdf("registration_receipt.html", registration_context(receipt, payment), path)
    db.commit()
    db.refresh(receipt)
    return receipt
