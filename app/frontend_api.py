"""Frontend compatibility routes for the static mmc-erp portal and React ERP client."""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import crud, models, receipt_service, schemas
from app.database import get_db
from app.document_storage import upload_application_documents


router = APIRouter(tags=["frontend-compat"])


class FrontendRegisterRequest(BaseModel):
    name: str | None = Field(None, max_length=120)
    email: EmailStr
    mobile: str | None = Field(None, max_length=20)
    mobile_number: str | None = Field(None, max_length=20)
    date_of_birth: date
    password: str = Field(..., min_length=8, max_length=128)


class FrontendLoginRequest(BaseModel):
    identifier: str | None = Field(None, max_length=160)
    email: str | None = Field(None, max_length=160)
    username: str | None = Field(None, max_length=160)
    password: str = Field(..., min_length=1, max_length=128)
    role: str = "auto"
    date_of_birth: date | None = None


class FrontendAdminLoginRequest(BaseModel):
    identifier: str | None = Field(None, max_length=160)
    email: str | None = None
    username: str | None = None
    password: str = Field(..., min_length=1, max_length=128)


class FrontendManualPaymentRequest(BaseModel):
    identifier: str = Field(..., min_length=1, max_length=160)
    payment_type: str = Field(..., min_length=1, max_length=80)
    amount: Decimal | None = None
    transaction_reference: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=255)


class FrontendPaymentRequest(BaseModel):
    transaction_id: str | None = None
    amount: Decimal | None = None
    mode: str = "CCAvenue"


class FrontendVerifyRequest(BaseModel):
    verified: bool = True


class FrontendShortlistRequest(BaseModel):
    shortlisted: bool = True
    allotted_category: str | None = None


class FrontendAllocateHostelRequest(BaseModel):
    hostel_name: str | None = None
    room_id: int | None = None
    room_number: str | None = None
    bed_number: str | None = None


class FrontendBulkShortlistRequest(BaseModel):
    student_ids: list[int] = Field(default_factory=list)
    allotted_category: str | None = None


class FrontendRoomAllocationRow(BaseModel):
    student_id: str | int | None = None
    application_id: str | int | None = None
    student_name: str | None = None
    course: str | None = None
    category: str | None = None
    merit_status: str | None = None
    hostel_name: str | None = None
    room_number: str | int | None = None
    bed_number: str | int | None = None


class FrontendRoomAllocationImportRequest(BaseModel):
    rows: list[FrontendRoomAllocationRow] = Field(default_factory=list)


PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 15
_forgot_password_attempts: dict[str, list[datetime]] = {}


def student_token(student_id: int) -> str:
    return f"mmc-student-{student_id}"


def admin_token(admin_id: int) -> str:
    return f"mmc-admin-{admin_id}"


def parse_bearer_token(authorization: str | None) -> tuple[str, int] | None:
    if not authorization:
        return None
    token = authorization.replace("Bearer", "", 1).strip()
    for prefix, role in (
        ("mmc-student-", "student"),
        ("mmc-admin-", "admin"),
        ("student-", "student"),
        ("admin-", "admin"),
    ):
        if token.startswith(prefix):
            try:
                return role, int(token[len(prefix) :])
            except ValueError:
                return None
    if token.isdigit():
        return "student", int(token)
    return None


def require_student(authorization: str | None, db: Session) -> models.Student:
    parsed = parse_bearer_token(authorization)
    if not parsed or parsed[0] != "student":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Student login required.")
    student = crud.get_student(db, parsed[1])
    if not student or not student.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Student session expired.")
    return student


def require_admin(authorization: str | None, db: Session) -> models.AdminUser:
    parsed = parse_bearer_token(authorization)
    if not parsed or parsed[0] != "admin":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin login required.")
    admin = db.get(models.AdminUser, parsed[1])
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin session expired.")
    return admin


def require_write_admin(authorization: str | None, db: Session) -> models.AdminUser:
    admin = require_admin(authorization, db)
    if (admin.role or "").lower() == "view_only":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This admin account has view-only access.")
    return admin


def map_frontend_fields(data: dict[str, Any]) -> dict[str, Any]:
    from app.main import normalize_application_data

    mapped = dict(data or {})
    aliases = {
        "mobile_number": "mobile",
        "aadhaar_number": "aadhar_number",
        "local_guardian_name": "guardian_name",
        "guardian_mobile_number": "guardian_mobile",
        "course_name": "course",
        "intermediate_college_name": "intermediate_college",
        "intermediate_board": "board",
        "honours_subject": "subject",
        "aggregate_percentage": "percentage",
        "admission_application_id": "admission_id",
        "applied_category": "applied_category",
        "category": "applied_category",
    }
    for source, target in aliases.items():
        if mapped.get(source) not in (None, "") and mapped.get(target) in (None, ""):
            mapped[target] = mapped[source]
    if mapped.get("mobile"):
        mapped["mobile"] = str(mapped["mobile"]).strip()[-10:]
    if mapped.get("guardian_mobile"):
        mapped["guardian_mobile"] = str(mapped["guardian_mobile"]).strip()[-10:]
    return normalize_application_data(mapped)


async def read_request_data(request: Request) -> dict[str, Any]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        return {key: value for key, value in form.items() if not hasattr(value, "filename")}
    if "application/json" in content_type:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    return {}


def application_completed(application: models.HostelApplication | None) -> bool:
    if not application:
        return False
    status_value = (application.application_status or application.status or "").lower()
    return status_value not in {"", "draft"}


def serialize_student_login(student: models.Student, db: Session) -> dict[str, Any]:
    application = crud.get_latest_student_application(db, student.id)
    return {
        "role": "student",
        "access_token": student_token(student.id),
        "token": student_token(student.id),
        "application_completed": application_completed(application),
        "application_number": student.student_code,
        "student_id": student.id,
        "student_name": student.name,
        "force_password_change": student.force_password_change,
        "user": {
            "id": student.id,
            "student_code": student.student_code,
            "application_number": student.student_code,
            "full_name": student.name,
            "name": student.name,
            "email": student.email,
            "mobile": student.mobile,
            "mobile_number": student.mobile,
            "date_of_birth": student.date_of_birth,
            "application_completed": application_completed(application),
            "force_password_change": student.force_password_change,
        },
    }


def serialize_admin_login(admin: models.AdminUser) -> dict[str, Any]:
    return {
        "role": admin.role,
        "access_token": admin_token(admin.id),
        "token": admin_token(admin.id),
        "username": admin.username,
        "email": admin.email,
        "full_name": admin.full_name,
        "token_type": "bearer",
        "user": {
            "id": admin.id,
            "username": admin.username,
            "email": admin.email,
            "full_name": admin.full_name,
            "role": admin.role,
            "is_active": admin.is_active,
        },
    }


def serialize_admin_student(student: models.Student, application: models.HostelApplication | None) -> dict[str, Any]:
    status_value = "Pending"
    verification_status = "pending"
    shortlist_status = "not_shortlisted"
    if application:
        status_value = application.application_status or application.status or "Pending"
        lowered = status_value.lower()
        if lowered in {"verified", "approved"}:
            verification_status = "verified"
        if lowered in {"room allocated", "room_allocated", "selected"}:
            shortlist_status = "room_allocated"
        elif lowered == "published":
            shortlist_status = "published"
        elif lowered == "shortlisted":
            shortlist_status = "shortlisted"
    hostel_name = application.hostel.name if application and application.hostel else None
    room_number = application.room.room_number if application and application.room else None
    documents = serialize_application_documents(application, include_data=False)
    payments = list(student.payments)
    receipts = list(student.receipts)
    registration_status = payment_status_for_kind(payments, receipts, "registration")
    hostel_status = payment_status_for_kind(payments, receipts, "hostel")
    hostel_payment_relevant = bool(
        (application and application.hostel_id)
        or any(payment_matches_kind(payment.payment_type, "hostel") for payment in payments)
        or any(receipt.receipt_type == "hostel_admission" for receipt in receipts)
    )
    overall_payment_status = combine_payment_status(registration_status, hostel_status) if hostel_payment_relevant else registration_status
    return {
        "id": student.id,
        "application_number": student.student_code,
        "student_code": student.student_code,
        "name": student.name,
        "email": student.email,
        "mobile_number": student.mobile,
        "course_name": (application.course if application else None) or student.course,
        "session": (application.session if application else None) or student.session,
        "category": (application.applied_category if application else None) or student.category,
        "allotted_category": application.allotted_category if application else None,
        "form_status": (application.application_status if application else "not_started").lower(),
        "verification_status": verification_status,
        "shortlist_status": shortlist_status,
        "allocated_hostel": hostel_name,
        "preferred_hostel": hostel_name,
        "room_number": room_number,
        "bed_number": application.bed if application else None,
        "application_id": application.id if application else None,
        "payment_status": overall_payment_status,
        "application_payment_status": registration_status,
        "registration_payment_status": registration_status,
        "hostel_status": hostel_status if hostel_payment_relevant else None,
        "hostel_payment_status": hostel_status if hostel_payment_relevant else None,
        "payment_history": [
            {
                "id": payment.id,
                "payment_type": payment.payment_type,
                "amount": payment.amount,
                "status": payment.status,
                "transaction_no": payment.transaction_no,
                "created_at": payment.created_at,
            }
            for payment in payments
        ],
        "account_active": student.is_active,
        "force_password_change": student.force_password_change,
        "aadhaar_number": application.aadhar_number if application else None,
        "summary": {
            "application_type": application.application_type if application else None,
            "admission_application_id": application.admission_id if application else None,
            "registration_date_of_birth": student.date_of_birth,
            "date_of_birth": student.date_of_birth,
            "gender": student.gender,
            "father_name": application.father_name if application else None,
            "mother_name": application.mother_name if application else None,
            "local_guardian_name": application.guardian_name if application else None,
            "guardian_mobile_number": application.guardian_mobile if application else None,
            "permanent_address": application.permanent_address if application else None,
            "correspondence_address": application.correspondence_address if application else None,
            "blood_group": application.blood_group if application else None,
            "aadhar_number": application.aadhar_number if application else None,
            "aadhaar_number": application.aadhar_number if application else None,
            "religion": application.religion if application else None,
            "nationality": application.nationality if application else None,
            "admission_level": application.admission_level if application else None,
            "college_name": application.college_name if application else None,
            "course_name": application.course if application else None,
            "session": application.session if application else None,
            "honours_subject": application.subject if application else None,
            "roll_number": application.roll_number if application else None,
            "intermediate_college_name": application.intermediate_college if application else None,
            "intermediate_board": application.board if application else None,
            "previous_course": application.previous_course if application else None,
            "result_type": application.result_type if application else None,
            "marks_obtained": application.marks_obtained if application else None,
            "total_marks": application.total_marks if application else None,
            "aggregate_percentage": application.percentage if application else None,
            "existing_hostel_name": application.existing_hostel_name if application else None,
            "existing_room_number": application.existing_room_number if application else None,
            "existing_bed_number": application.existing_bed_number if application else None,
            "existing_block": application.existing_block if application else None,
            "existing_floor": application.existing_floor if application else None,
            "existing_previous_session": application.existing_previous_session if application else None,
            "documents": documents,
            **documents,
        },
    }


def normalize_payment_state(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in {"paid", "success", "successful", "completed", "captured"}:
        return "paid"
    if text in {"failed", "failure", "declined", "cancelled", "canceled", "aborted"}:
        return "failed"
    if text in {"refunded", "refund"}:
        return "refunded"
    return "pending"


def payment_matches_kind(payment_type: str | None, kind: str) -> bool:
    text = (payment_type or "").strip().lower()
    if kind == "hostel":
        return "hostel" in text
    return "hostel" not in text and ("registration" in text or "application" in text or "admission" in text)


def payment_status_for_kind(
    payments: list[models.Payment],
    receipts: list[models.PaymentReceipt],
    kind: str,
) -> str:
    matching_receipts = [
        receipt for receipt in receipts
        if (kind == "hostel" and receipt.receipt_type == "hostel_admission")
        or (kind == "registration" and receipt.receipt_type == "application_registration")
    ]
    if matching_receipts:
        return "paid"
    matching_payments = [payment for payment in payments if payment_matches_kind(payment.payment_type, kind)]
    states = [normalize_payment_state(payment.status) for payment in matching_payments]
    if "paid" in states:
        return "paid"
    if "failed" in states:
        return "failed"
    if "refunded" in states:
        return "refunded"
    return "pending"


def combine_payment_status(registration_status: str, hostel_status: str) -> str:
    statuses = {registration_status, hostel_status}
    if "failed" in statuses:
        return "failed"
    if "refunded" in statuses:
        return "refunded"
    if statuses == {"paid"}:
        return "paid"
    if "paid" in statuses:
        return "partially_paid"
    return "pending"


def payment_type_key(value: str | None) -> str:
    return "hostel" if "hostel" in (value or "").lower() else "registration"


def receipt_type_key(value: str | None) -> str:
    return "hostel" if value == "hostel_admission" else "registration"


def serialize_admin_payment(payment: models.Payment) -> dict[str, Any]:
    receipt = payment.receipts[0] if payment.receipts else None
    application = payment.application
    student = payment.student
    payment_date = payment.paid_at or payment.created_at
    return {
        "id": payment.id,
        "payment_id": payment.id,
        "receipt_id": receipt.id if receipt else None,
        "receipt_number": receipt.receipt_number if receipt else None,
        "receipt_url": f"/receipts/{receipt.id}/download" if receipt else None,
        "application_number": (
            application.application_no if application else receipt.application_number if receipt else None
        ),
        "student_id": payment.student_id,
        "student_code": student.student_code if student else None,
        "student_name": student.name if student else None,
        "student_email": student.email if student else None,
        "student_mobile": student.mobile if student else None,
        "payment_type": payment.payment_type,
        "payment_type_key": payment_type_key(payment.payment_type),
        "transaction_id": payment.transaction_no,
        "tracking_id": payment.tracking_id or payment.transaction_no,
        "bank_ref_no": payment.bank_ref_no,
        "order_id": payment.transaction_no,
        "currency": payment.currency,
        "sub_account_id": payment.sub_account_id,
        "failure_reason": payment.failure_reason,
        "amount": payment.amount,
        "mode": payment.mode,
        "status": payment.status,
        "status_key": normalize_payment_state(payment.status),
        "payment_date": payment_date,
        "created_at": payment.created_at,
    }


def serialize_admin_receipt_only(receipt: models.PaymentReceipt) -> dict[str, Any]:
    payment = receipt.payment
    student = receipt.student or (payment.student if payment else None)
    application = payment.application if payment else None
    payment_type = (
        payment.payment_type
        if payment and payment.payment_type
        else ("Hostel Admission Fee" if receipt.receipt_type == "hostel_admission" else "Registration Fee")
    )
    payment_date = payment.paid_at if payment and payment.paid_at else receipt.generated_at
    return {
        "id": f"receipt-{receipt.id}",
        "payment_id": payment.id if payment else None,
        "receipt_id": receipt.id,
        "receipt_number": receipt.receipt_number,
        "receipt_url": f"/receipts/{receipt.id}/download",
        "application_number": application.application_no if application else receipt.application_number,
        "student_id": receipt.student_id,
        "student_code": student.student_code if student else None,
        "student_name": student.name if student else None,
        "student_email": student.email if student else None,
        "student_mobile": student.mobile if student else None,
        "payment_type": payment_type,
        "payment_type_key": receipt_type_key(receipt.receipt_type),
        "transaction_id": payment.transaction_no if payment else receipt.transaction_id,
        "tracking_id": (payment.tracking_id or payment.transaction_no) if payment else receipt.transaction_id,
        "bank_ref_no": payment.bank_ref_no if payment else None,
        "order_id": payment.transaction_no if payment else receipt.transaction_id,
        "currency": payment.currency if payment else None,
        "sub_account_id": payment.sub_account_id if payment else None,
        "failure_reason": payment.failure_reason if payment else None,
        "amount": receipt.amount,
        "mode": payment.mode if payment else None,
        "status": payment.status if payment and payment.status else "Generated",
        "status_key": "paid",
        "payment_date": payment_date,
        "created_at": receipt.generated_at,
    }


def serialize_application_documents(application: models.HostelApplication | None, include_data: bool = True) -> dict[str, Any]:
    if not include_data and application is not None and hasattr(application, "_document_flags"):
        flags = getattr(application, "_document_flags") or {}
        return {
            "student_photo_data": bool(flags.get("student_photo_data")),
            "aadhar_card_data": bool(flags.get("aadhar_card_data")),
            "admission_receipt_data": bool(flags.get("admission_receipt_data")),
            "income_certificate_data": bool(flags.get("income_certificate_data")),
            "caste_certificate_data": bool(flags.get("caste_certificate_data")),
        }
    values = {
        "student_photo_data": application.student_photo_data if application else None,
        "aadhar_card_data": application.aadhar_card_data if application else None,
        "admission_receipt_data": application.admission_receipt_data if application else None,
        "income_certificate_data": application.income_certificate_data if application else None,
        "caste_certificate_data": application.caste_certificate_data if application else None,
    }
    if include_data:
        return values
    return {key: bool(value) for key, value in values.items()}


def clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_mobile(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    digits = "".join(char for char in text if char.isdigit())
    return digits or text


def normalize_aadhaar(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    digits = "".join(char for char in text if char.isdigit())
    return digits or text


def release_deleted_student_unique_fields(db: Session, student: models.Student) -> None:
    deleted_key = f"deleted{student.id}"
    if not str(student.student_code or "").startswith(f"{deleted_key}-"):
        student.student_code = f"{deleted_key}-{student.student_code}"[:32]
    if not str(student.email or "").startswith(f"{deleted_key}-"):
        student.email = f"{deleted_key}-{student.email}"[:160]
    if not str(student.mobile or "").startswith(deleted_key):
        student.mobile = f"{deleted_key}-{student.mobile}"[:20]
    db.add(student)


def release_existing_deleted_conflicts(db: Session, email: str | None = None, mobile: str | None = None) -> None:
    checks = []
    if email:
        checks.append(models.Student.email == email)
    if mobile:
        checks.append(models.Student.mobile == mobile)
    if not checks:
        return
    deleted_students = list(
        db.scalars(
            select(models.Student).where(
                models.Student.is_active.is_(False),
                or_(*checks),
            )
        )
    )
    for student in deleted_students:
        release_deleted_student_unique_fields(db, student)
    if deleted_students:
        db.commit()


def log_activity(
    db: Session,
    *,
    entity_type: str,
    entity_id: str | int,
    action: str,
    admin_id: int | None = None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
) -> None:
    db.add(
        models.ActivityLog(
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            admin_id=admin_id,
            old_values=json.dumps(old_values or {}, default=str),
            new_values=json.dumps(new_values or {}, default=str),
        )
    )


def send_account_email(recipient: str | None, subject: str, body: str) -> str:
    if not recipient:
        return "skipped"
    return "skipped"


def enforce_forgot_password_rate_limit(request: Request, email: str) -> None:
    now = datetime.utcnow()
    client = request.client.host if request.client else "anonymous"
    key = f"{client}:{email.lower()}"
    attempts = [item for item in _forgot_password_attempts.get(key, []) if now - item < timedelta(minutes=15)]
    if len(attempts) >= 5:
        _forgot_password_attempts[key] = attempts
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many reset attempts. Please try again later.")
    attempts.append(now)
    _forgot_password_attempts[key] = attempts


def serialize_application_form(student: models.Student, application: models.HostelApplication | None) -> dict[str, Any]:
    if not application:
        return {
            "application_number": None,
            "form_status": "not_started",
            "registration_date_of_birth": student.date_of_birth,
            "data": {
                "name": student.name,
                "email": student.email,
                "mobile_number": student.mobile,
                "date_of_birth": student.date_of_birth,
                "category": student.category,
            },
        }
    form_status = "draft" if (application.application_status or "").lower() == "draft" else "submitted"
    data = {
        "name": student.name,
        "email": student.email,
        "mobile_number": student.mobile,
        "date_of_birth": student.date_of_birth,
        "gender": student.gender,
        "category": student.category,
        "application_type": application.application_type,
        "admission_level": application.admission_level,
        "admission_id": application.admission_id,
        "college_name": application.college_name,
        "course_name": application.course,
        "session": application.session,
        "father_name": application.father_name,
        "mother_name": application.mother_name,
        "local_guardian_name": application.guardian_name,
        "guardian_mobile_number": application.guardian_mobile,
        "permanent_address": application.permanent_address,
        "correspondence_address": application.correspondence_address,
        "blood_group": application.blood_group,
        "aadhaar_number": application.aadhar_number,
        "religion": application.religion,
        "nationality": application.nationality,
        "student_photo_data": application.student_photo_data,
        "aadhar_card_data": application.aadhar_card_data,
        "admission_receipt_data": application.admission_receipt_data,
        "income_certificate_data": application.income_certificate_data,
        "caste_certificate_data": application.caste_certificate_data,
        "intermediate_college_name": application.intermediate_college,
        "intermediate_board": application.board,
        "previous_course": application.previous_course,
        "result_type": application.result_type,
        "marks_obtained": application.marks_obtained,
        "total_marks": application.total_marks,
        "aggregate_percentage": application.percentage,
        "roll_number": application.roll_number,
        "honours_subject": application.subject,
        "applied_category": application.applied_category,
        "allotted_category": application.allotted_category,
        "existing_hostel_name": application.existing_hostel_name,
        "existing_room_number": application.existing_room_number,
        "existing_bed_number": application.existing_bed_number,
        "existing_block": application.existing_block,
        "existing_floor": application.existing_floor,
        "existing_previous_session": application.existing_previous_session,
    }
    return {
        "application_number": application.application_no,
        "application_type": application.application_type,
        "form_status": form_status,
        "current_step": application.current_step,
        "registration_date_of_birth": student.date_of_birth,
        "data": data,
        "application": {
            "id": application.id,
            "application_no": application.application_no,
            "status": application.application_status,
            "current_step": application.current_step,
            **data,
        },
    }


def build_student_dashboard(student: models.Student, db: Session) -> dict[str, Any]:
    receipt_service.ensure_receipts_for_successful_payments(db, student_id=student.id)
    application = crud.get_latest_student_application(db, student.id)
    payments = crud.list_payments(db, student_id=student.id)
    receipts = crud.list_receipts(db, student_id=student.id)
    registration_payment = next(
        (
            payment for payment in payments
            if "registration" in (payment.payment_type or "").lower()
            and normalize_payment_state(payment.status) == "paid"
        ),
        None,
    )
    hostel_payment = next(
        (
            payment for payment in payments
            if "hostel" in (payment.payment_type or "").lower()
            and normalize_payment_state(payment.status) == "paid"
        ),
        None,
    )
    registration_receipt = next(
        (receipt for receipt in receipts if receipt.receipt_type == "application_registration"),
        None,
    )
    hostel_receipt = next(
        (receipt for receipt in receipts if receipt.receipt_type == "hostel_admission"),
        None,
    )
    status_value = application.application_status if application else "Not Started"
    shortlisted = bool(application and (application.application_status or "").lower() in {"shortlisted", "published", "room allocated", "selected", "approved"})
    return {
        "student_name": student.name,
        "name": student.name,
        "email": student.email,
        "mobile_number": student.mobile,
        "application_number": application.application_no if application else student.student_code,
        "application_no": application.application_no if application else None,
        "application_status": status_value,
        "form_status": (application.application_status if application else "not_started").lower(),
        "shortlisted": shortlisted,
        "application_payment_status": "paid" if (registration_payment or registration_receipt) else "pending",
        "hostel_payment_status": "paid" if (hostel_payment or hostel_receipt) else "pending",
        "hostel_receipt": bool(hostel_receipt),
        "allocated_hostel": application.hostel.name if application and application.hostel else None,
        "preferred_hostel": application.hostel.name if application and application.hostel else None,
        "room_number": application.room.room_number if application and application.room else None,
        "bed_number": application.bed if application else None,
        "payment_history": [
            {
                "id": payment.id,
                "payment_type": payment.payment_type,
                "amount": payment.amount,
                "status": payment.status,
                "created_at": payment.created_at,
            }
            for payment in payments
        ],
        "application_receipt": registration_receipt,
        "hostel_receipt": hostel_receipt,
        "summary": serialize_application_form(student, application),
    }


def build_admin_dashboard(db: Session) -> dict[str, Any]:
    students = crud.list_students(db, limit=5000)
    applications = crud.list_applications(db)
    payments = crud.list_payments(db)
    rooms = crud.list_rooms(db)
    occupied_room_ids = {
        application.room_id
        for application in applications
        if application.room_id and (application.application_status or "").lower() in {"selected", "approved", "shortlisted"}
    }
    total_beds = sum(room.beds for room in rooms)
    occupied_beds = sum(room.beds for room in rooms if room.id in occupied_room_ids or room.status == "occupied")
    application_revenue = sum(
        payment.amount
        for payment in payments
        if "registration" in (payment.payment_type or "").lower()
        and (payment.status or "").lower() in {"paid", "success", "completed"}
    )
    hostel_revenue = sum(
        payment.amount
        for payment in payments
        if "hostel" in (payment.payment_type or "").lower()
        and (payment.status or "").lower() in {"paid", "success", "completed"}
    )
    by_category: dict[str, int] = {}
    by_course: dict[str, int] = {}
    verified_students = 0
    shortlisted_students = 0
    pending_applications = 0
    for application in applications:
        category = application.applied_category or "Unknown"
        course = application.course or "Unknown"
        by_category[category] = by_category.get(category, 0) + 1
        by_course[course] = by_course.get(course, 0) + 1
        status_value = (application.application_status or "").lower()
        if status_value in {"verified", "approved", "selected"}:
            verified_students += 1
        if status_value in {"shortlisted", "selected"}:
            shortlisted_students += 1
        if status_value in {"submitted", "pending", "draft"}:
            pending_applications += 1
    return {
        "total_applications": len(applications),
        "verified_students": verified_students,
        "shortlisted_students": shortlisted_students,
        "pending_applications": pending_applications,
        "application_revenue": float(application_revenue),
        "hostel_revenue": float(hostel_revenue),
        "occupied_beds": occupied_beds,
        "available_beds": max(total_beds - occupied_beds, 0),
        "by_category": [{"label": key, "value": value} for key, value in sorted(by_category.items())],
        "by_course": [{"label": key, "value": value} for key, value in sorted(by_course.items())],
    }


def normalize_manual_payment_type(value: str | None) -> str:
    text_value = (value or "").strip().lower()
    if "hostel" in text_value:
        return "Hostel Admission Fee"
    if "registration" in text_value or "application" in text_value:
        return "Registration Fee"
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Select registration fee or hostel fee.")


def receipt_type_for_payment(payment_type: str) -> str:
    return "hostel_admission" if "hostel" in payment_type.lower() else "application_registration"


def resolve_manual_payment_application(db: Session, identifier: str) -> models.HostelApplication:
    value = (identifier or "").strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter student code, application number, email, mobile, or admission ID.")
    lowered = value.lower()
    application = db.scalar(
        select(models.HostelApplication)
        .where(or_(models.HostelApplication.application_no == value, models.HostelApplication.admission_id == value))
        .order_by(models.HostelApplication.updated_at.desc(), models.HostelApplication.id.desc())
        .limit(1)
    )
    if not application and value.isdigit():
        application = db.get(models.HostelApplication, int(value))
    if application:
        return application
    student = db.scalar(
        select(models.Student)
        .where(
            or_(
                models.Student.student_code == value,
                models.Student.email == lowered,
                models.Student.mobile == value,
                models.Student.name == value,
            )
        )
        .limit(1)
    )
    if not student and value.isdigit():
        student = db.get(models.Student, int(value))
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student or application not found.")
    application = crud.get_latest_student_application(db, student.id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No application found for this student.")
    return application


def expected_manual_payment_amount(application: models.HostelApplication, payment_type: str) -> Decimal:
    if payment_type == "Registration Fee":
        app_type = (application.application_type or "new").strip().lower()
        return Decimal("100") if app_type in {"existing", "renewal"} else Decimal("1000")
    if not application.hostel:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hostel allotment is required before hostel fee can be completed.")
    hostel_fee = application.hostel.fee or Decimal("0")
    if hostel_fee:
        return Decimal(hostel_fee)
    hostel_name = (application.hostel.name or "").lower()
    if "mahima" in hostel_name:
        return Decimal("12000")
    if "vaidehi" in hostel_name:
        return Decimal("10000")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hostel fee is not configured for this hostel.")


def application_receipt_exists(db: Session, application: models.HostelApplication, receipt_type: str) -> bool:
    return bool(
        db.scalar(
            select(models.PaymentReceipt.id)
            .where(
                models.PaymentReceipt.student_id == application.student_id,
                models.PaymentReceipt.receipt_type == receipt_type,
                models.PaymentReceipt.application_number == application.application_no,
            )
            .limit(1)
        )
    )


def successful_application_payment_exists(db: Session, application: models.HostelApplication, payment_type: str) -> bool:
    return bool(
        crud.get_successful_payment_for_application(db, application.id, payment_type)
        or application_receipt_exists(db, application, receipt_type_for_payment(payment_type))
    )


def validate_manual_payment(db: Session, application: models.HostelApplication, payment_type: str, amount: Decimal) -> None:
    if (application.application_status or application.status or "").lower() == "draft":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submit the application before completing payment.")
    expected_amount = expected_manual_payment_amount(application, payment_type)
    if amount != expected_amount:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{payment_type} amount must be Rs. {expected_amount:.2f}.")
    if successful_application_payment_exists(db, application, payment_type):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{payment_type} is already completed for this application.")
    if payment_type == "Hostel Admission Fee":
        if not successful_application_payment_exists(db, application, "Registration Fee"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Complete registration fee before hostel fee.")
        if (application.application_status or "").lower() not in {"shortlisted", "published", "room allocated", "selected", "approved"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Application must be shortlisted before hostel fee.")
        if not application.hostel_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hostel allotment is required before hostel fee.")


def list_admin_rooms(db: Session) -> dict[str, list[dict[str, Any]]]:
    rooms = crud.list_rooms(db)
    applications = crud.list_applications(db)
    occupied_counts: dict[int, int] = {}
    for application in applications:
        if (
            application.room_id
            and application.bed
            and (application.allocation_status or "") != "vacated"
            and (application.application_status or "") != "Draft"
        ):
            occupied_counts[application.room_id] = occupied_counts.get(application.room_id, 0) + 1
    items = []
    for room in rooms:
        hostel = room.hostel
        occupied_applications = [
            application
            for application in applications
            if application.room_id == room.id
            and (application.allocation_status or "") != "vacated"
            and (application.application_status or "") != "Draft"
            and application.bed
        ]
        occupied_bed_numbers = sorted({crud.normalize_bed_value(application.bed) for application in occupied_applications if application.bed})
        bed_labels = ["A", "B", "C"][: max(int(room.beds or 0), 0)]
        available_bed_numbers = [bed for bed in bed_labels if bed not in occupied_bed_numbers]
        occupied = occupied_counts.get(room.id, 0)
        if room.status == "occupied" and occupied == 0:
            occupied = room.beds
        available_beds = len(available_bed_numbers)
        items.append(
            {
                "id": room.id,
                "hostel_name": hostel.name if hostel else "",
                "room_number": room.room_number,
                "block_name": str(room.floor),
                "bed_capacity": room.beds,
                "available_beds": available_beds,
                "occupied_bed_numbers": occupied_bed_numbers,
                "available_bed_numbers": available_bed_numbers,
                "available_bed_labels": available_bed_numbers,
                "status": "available" if available_beds > 0 else "occupied",
            }
        )
    return {"items": items}


def _normalize_sheet_value(value: Any) -> str:
    return str(value or "").strip()


def _normalize_sheet_key(value: Any) -> str:
    return _normalize_sheet_value(value).lower()


def _latest_applications_by_student(db: Session) -> dict[int, models.HostelApplication]:
    latest: dict[int, models.HostelApplication] = {}
    for application in crud.list_applications(db):
        current = latest.get(application.student_id)
        if not current or application.id > current.id:
            latest[application.student_id] = application
    return latest


def _match_allocation_row(
    row: FrontendRoomAllocationRow,
    students: list[models.Student],
    applications: list[models.HostelApplication],
    latest_by_student: dict[int, models.HostelApplication],
) -> tuple[models.Student | None, models.HostelApplication | None]:
    student_tokens = [_normalize_sheet_value(row.student_id)]
    application_tokens = [_normalize_sheet_value(row.application_id)]
    for token in application_tokens:
        if not token:
            continue
        for application in applications:
            if token == str(application.id) or token.lower() == (application.application_no or "").lower():
                return application.student, application
        for student in students:
            if token.lower() == (student.student_code or "").lower():
                return student, latest_by_student.get(student.id)
    for token in student_tokens:
        if not token:
            continue
        for student in students:
            if token == str(student.id) or token.lower() == (student.student_code or "").lower():
                return student, latest_by_student.get(student.id)
    return None, None


def validate_room_allocation_rows(
    db: Session,
    rows: list[FrontendRoomAllocationRow],
) -> dict[str, Any]:
    students = [student for student in crud.list_students(db, limit=10000) if student.is_active]
    applications = crud.list_applications(db)
    latest_by_student = _latest_applications_by_student(db)
    hostels = {hostel.name.lower(): hostel for hostel in crud.list_hostels(db)}
    rooms = crud.list_rooms(db)
    rooms_by_key = {
        (room.hostel.name.lower() if room.hostel else "", str(room.room_number).strip().lower()): room
        for room in rooms
    }
    existing_beds: dict[tuple[int, str], int] = {}
    for application in applications:
        if not application.room_id or not application.bed:
            continue
        if (application.allocation_status or "") == "vacated" or (application.application_status or "") == "Draft":
            continue
        bed = crud.normalize_bed_value(application.bed)
        if bed:
            existing_beds[(application.room_id, bed)] = application.id
    upload_beds: dict[tuple[int, str], int] = {}
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        student, application = _match_allocation_row(row, students, applications, latest_by_student)
        result = {
            "row_number": index,
            "status": "failed",
            "message": "",
            "student_id": student.student_code if student else _normalize_sheet_value(row.student_id),
            "application_id": application.application_no if application else _normalize_sheet_value(row.application_id),
            "student_name": student.name if student else _normalize_sheet_value(row.student_name),
            "hostel_name": _normalize_sheet_value(row.hostel_name),
            "room_number": _normalize_sheet_value(row.room_number),
            "bed_number": _normalize_sheet_value(row.bed_number),
        }
        if not student or not application:
            result["message"] = "Student/Application ID not found."
            results.append(result)
            continue
        if not result["hostel_name"] and not result["room_number"] and not result["bed_number"]:
            result["status"] = "skipped"
            result["message"] = "No room allocation data provided."
            results.append(result)
            continue
        hostel = hostels.get(result["hostel_name"].lower())
        if not hostel:
            result["message"] = "Hostel name not found."
            results.append(result)
            continue
        room = rooms_by_key.get((hostel.name.lower(), result["room_number"].lower()))
        if not room:
            result["message"] = "Room number not found in the selected hostel."
            results.append(result)
            continue
        bed = crud.normalize_bed_value(result["bed_number"])
        if bed not in {"A", "B", "C"}:
            result["message"] = "Bed number must be A, B, or C."
            results.append(result)
            continue
        existing_application_id = existing_beds.get((room.id, bed))
        if existing_application_id and existing_application_id != application.id:
            result["message"] = "Room/bed is already allocated to another student."
            results.append(result)
            continue
        upload_owner = upload_beds.get((room.id, bed))
        if upload_owner and upload_owner != application.id:
            result["message"] = "Duplicate room/bed in uploaded sheet."
            results.append(result)
            continue
        upload_beds[(room.id, bed)] = application.id
        result.update(
            {
                "status": "valid",
                "message": "Ready to allocate.",
                "student_db_id": student.id,
                "application_db_id": application.id,
                "room_id": room.id,
                "bed_number": bed,
            }
        )
        results.append(result)
    return {
        "items": results,
        "summary": {
            "total": len(results),
            "valid": sum(1 for item in results if item["status"] == "valid"),
            "failed": sum(1 for item in results if item["status"] == "failed"),
            "skipped": sum(1 for item in results if item["status"] == "skipped"),
        },
    }


def resolve_login_identifier(payload: FrontendLoginRequest | FrontendAdminLoginRequest) -> str:
    return (payload.identifier or payload.email or payload.username or "").strip()


def login_student_or_admin(payload: FrontendLoginRequest, db: Session) -> dict[str, Any]:
    identifier = resolve_login_identifier(payload)
    if not identifier:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Email or identifier is required.")
    login_payload = schemas.LoginRequest(identifier=identifier, password=payload.password, role=payload.role)
    if login_payload.role == "admin":
        admin = crud.authenticate_admin(db, login_payload.identifier, login_payload.password)
        if not admin:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials.")
        return serialize_admin_login(admin)
    if login_payload.role == "student":
        student = crud.authenticate_student(db, login_payload.identifier, login_payload.password)
        if not student:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid student credentials.")
        return serialize_student_login(student, db)
    student = crud.authenticate_student(db, login_payload.identifier, login_payload.password)
    if student:
        return serialize_student_login(student, db)
    admin = crud.authenticate_admin(db, login_payload.identifier, login_payload.password)
    if admin:
        return serialize_admin_login(admin)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login credentials.")


def latest_application_for_aadhaar(student: models.Student, aadhaar_number: str) -> models.HostelApplication | None:
    normalized = normalize_aadhaar(aadhaar_number)
    applications = sorted(student.applications, key=lambda item: item.updated_at or item.created_at or datetime.min, reverse=True)
    for application in applications:
        if normalize_aadhaar(application.aadhar_number) == normalized:
            return application
    return None


def register_student_compat(payload: FrontendRegisterRequest, db: Session) -> dict[str, Any]:
    from app.main import save_or_409

    mobile = (payload.mobile_number or payload.mobile or "").strip()
    if len(mobile) > 10:
        mobile = mobile[-10:]
    name = (payload.name or payload.email.split("@")[0]).strip()
    email = str(payload.email).strip().lower()
    release_existing_deleted_conflicts(db, email=email, mobile=mobile)
    register_payload = schemas.StudentRegister(
        name=name,
        email=email,
        mobile=mobile,
        date_of_birth=payload.date_of_birth,
        password=payload.password,
    )
    student = save_or_409(lambda: crud.register_student(db, register_payload))
    return {
        "id": student.id,
        "application_number": student.student_code,
        "student_code": student.student_code,
        "name": student.name,
        "email": student.email,
        "mobile_number": student.mobile,
        "message": "Registration completed successfully.",
    }


async def save_or_submit_application(
    student: models.Student,
    request: Request,
    db: Session,
    *,
    submit: bool,
) -> dict[str, Any]:
    from app.main import require_admission_open, validate_step_payload

    raw_data = map_frontend_fields(await read_request_data(request))
    current_step = int(raw_data.get("current_step") or 8)
    existing_draft = crud.get_editable_student_application(db, student.id)
    if submit:
        application = existing_draft or crud.get_latest_student_application(db, student.id)
        if not application:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No draft application found to submit.")
        require_admission_open(db, existing_draft=True)
        for step in range(1, 9):
            merged = {field: getattr(application, field, None) for field in crud.APPLICATION_DRAFT_FIELDS}
            merged.update(raw_data)
            validate_step_payload(step, merged)
        previous_documents = {
            field: getattr(application, field, None)
            for field in ("student_photo_data", "aadhar_card_data", "admission_receipt_data", "income_certificate_data", "caste_certificate_data")
        }
        raw_data = upload_application_documents(raw_data, student_id=student.id, application_id=application.id, previous_values=previous_documents)
        updated = crud.save_application_draft(db, student, 8, raw_data)
        submitted = crud.submit_application(db, updated)
        return {
            "message": "Application submitted successfully.",
            "application_number": submitted.application_no,
            "form_status": "submitted",
            "application": serialize_application_form(student, submitted)["application"],
        }
    require_admission_open(db, existing_draft=bool(existing_draft))
    validate_step_payload(current_step, raw_data)
    previous_documents = {
        field: getattr(existing_draft, field, None)
        for field in ("student_photo_data", "aadhar_card_data", "admission_receipt_data", "income_certificate_data", "caste_certificate_data")
    } if existing_draft else {}
    raw_data = upload_application_documents(
        raw_data,
        student_id=student.id,
        application_id=existing_draft.id if existing_draft else None,
        previous_values=previous_documents,
    )
    saved = crud.save_application_draft(db, student, current_step, raw_data)
    return serialize_application_form(student, saved)


@router.post("/register")
@router.post("/api/register")
def frontend_register(payload: FrontendRegisterRequest, db: Session = Depends(get_db)):
    return register_student_compat(payload, db)


@router.post("/login")
@router.post("/api/login")
def frontend_login(payload: FrontendLoginRequest, db: Session = Depends(get_db)):
    return login_student_or_admin(payload, db)


@router.post("/forgot-password")
@router.post("/api/forgot-password")
def frontend_forgot_password(
    payload: schemas.StudentForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    aadhaar_number = normalize_aadhaar(payload.aadhaar_number)
    enforce_forgot_password_rate_limit(request, email)
    student = db.scalar(select(models.Student).where(models.Student.email == email, models.Student.is_active.is_(True)))
    application = latest_application_for_aadhaar(student, aadhaar_number) if student else None
    if not student or not application:
        log_activity(
            db,
            entity_type="student",
            entity_id="unknown",
            action="password_reset_request_failed",
            new_values={"email": email, "reason": "invalid_email_or_aadhaar"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Email or Aadhaar Number")

    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    student.reset_token_hash = crud.hash_reset_token(token)
    student.reset_token_expires_at = now + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    student.reset_requested_at = now
    student.reset_last_attempt_at = now
    student.reset_attempt_count = int(student.reset_attempt_count or 0) + 1
    db.add(student)
    email_status = send_account_email(
        student.email,
        "Student password reset link",
        f"Use this reset token within {PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes: {token}",
    )
    log_activity(
        db,
        entity_type="student",
        entity_id=student.id,
        action="password_reset_requested",
        new_values={"email": email, "expires_at": student.reset_token_expires_at, "email_status": email_status},
    )
    db.commit()
    message = "Password Reset Email Sent" if email_status != "skipped" else f"Password reset token: {token}"
    return {"message": message}


@router.post("/complete-password-reset")
@router.post("/api/complete-password-reset")
def frontend_complete_password_reset(
    payload: schemas.StudentCompletePasswordResetRequest,
    db: Session = Depends(get_db),
):
    password_error = crud.validate_password_strength(payload.new_password, payload.confirm_password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=password_error)
    token_hash = crud.hash_reset_token(payload.token)
    student = db.scalar(select(models.Student).where(models.Student.reset_token_hash == token_hash))
    now = datetime.utcnow()
    if not student or not student.reset_token_expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset link.")
    if student.reset_token_expires_at < now:
        student.reset_token_hash = None
        student.reset_token_expires_at = None
        db.add(student)
        log_activity(db, entity_type="student", entity_id=student.id, action="password_reset_expired")
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password reset link has expired.")

    student.password_hash = crud.hash_password(payload.new_password)
    student.force_password_change = False
    student.reset_token_hash = None
    student.reset_token_expires_at = None
    student.reset_attempt_count = 0
    student.reset_last_attempt_at = None
    db.add(student)
    log_activity(db, entity_type="student", entity_id=student.id, action="password_reset_completed")
    db.commit()
    return {"message": "Password Changed Successfully"}


@router.post("/api/admin/login")
@router.post("/api/auth/admin/login")
def frontend_admin_login(payload: FrontendAdminLoginRequest, db: Session = Depends(get_db)):
    identifier = (payload.identifier or payload.username or payload.email or "").strip()
    return login_student_or_admin(
        FrontendLoginRequest(identifier=identifier, password=payload.password, role="admin"),
        db,
    )


@router.get("/dashboard")
@router.get("/api/dashboard")
def frontend_dashboard(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    student = require_student(authorization, db)
    return build_student_dashboard(student, db)


@router.get("/application")
@router.get("/api/application")
def frontend_application(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    student = require_student(authorization, db)
    application = crud.get_latest_student_application(db, student.id)
    return serialize_application_form(student, application)


@router.post("/application/draft")
@router.post("/api/application/draft")
async def frontend_application_draft(
    request: Request,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    student = require_student(authorization, db)
    return await save_or_submit_application(student, request, db, submit=False)


@router.post("/application/submit")
@router.post("/api/application/submit")
async def frontend_application_submit(
    request: Request,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    student = require_student(authorization, db)
    return await save_or_submit_application(student, request, db, submit=True)


@router.post("/payment/application")
@router.post("/api/payment/application")
def frontend_payment_application(
    payload: FrontendPaymentRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_student(authorization, db)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This client-confirmed payment endpoint has been removed. Use /api/payment/initiate.",
    )


@router.post("/payment/hostel")
@router.post("/api/payment/hostel")
def frontend_payment_hostel(
    payload: FrontendPaymentRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_student(authorization, db)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="This client-confirmed payment endpoint has been removed. Use /api/payment/initiate.",
    )


@router.get("/admin/dashboard")
@router.get("/api/admin/dashboard")
def frontend_admin_dashboard(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    return build_admin_dashboard(db)


@router.get("/admin/students")
@router.get("/api/admin/students")
def frontend_admin_students(
    limit: int = 500,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    rows = crud.list_active_students_with_latest_applications(db, limit=max(limit, 5000))
    items = [serialize_admin_student(student, application) for student, application in rows]
    return {"items": items}


@router.get("/admin/students/{student_id}/documents")
@router.get("/api/admin/students/{student_id}/documents")
def frontend_admin_student_documents(
    student_id: int,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    application = crud.get_latest_student_application(db, student_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    return {
        "student_id": student_id,
        "application_id": application.id,
        "documents": serialize_application_documents(application, include_data=True),
    }


@router.get("/admin/payments")
@router.get("/api/admin/payments")
def frontend_admin_payments(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    receipt_service.ensure_receipts_for_successful_payments(db, max_generate=2)
    payments = crud.list_payments(db)
    receipts = crud.list_receipts(db)
    payment_ids_with_receipts = {receipt.payment_id for receipt in receipts if receipt.payment_id}
    items = [serialize_admin_receipt_only(receipt) for receipt in receipts]
    items.extend(
        serialize_admin_payment(payment)
        for payment in payments
        if payment.id not in payment_ids_with_receipts
    )
    items.sort(key=lambda item: item.get("payment_date") or datetime.min, reverse=True)
    return {"items": items}


@router.post("/admin/payments/manual", response_model=schemas.PaymentReceiptRead)
@router.post("/api/admin/payments/manual", response_model=schemas.PaymentReceiptRead)
def frontend_admin_manual_payment(
    payload: FrontendManualPaymentRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    admin = require_write_admin(authorization, db)
    application = resolve_manual_payment_application(db, payload.identifier)
    student = application.student
    if not student or not student.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active student account not found for this application.")
    payment_type = normalize_manual_payment_type(payload.payment_type)
    amount = payload.amount if payload.amount is not None else expected_manual_payment_amount(application, payment_type)
    validate_manual_payment(db, application, payment_type, amount)
    timestamp = datetime.now()
    reference = (payload.transaction_reference or "").strip()
    transaction_no = reference or f"MANUAL-{timestamp.strftime('%Y%m%d%H%M%S%f')}-{application.id}"[:50]
    if crud.get_payment_by_transaction_no(db, transaction_no):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Transaction reference already exists.")
    payment = crud.create_payment(
        db,
        schemas.PaymentCreate(
            student_id=student.id,
            application_id=application.id,
            payment_type=payment_type,
            amount=amount,
            currency="INR",
            mode="Manual Admin Entry",
            status="Paid",
            tracking_id=transaction_no,
            bank_ref_no=reference or None,
            failure_reason="",
            sub_account_id="Manual",
            gateway_response=json.dumps(
                {
                    "source": "admin_manual_payment",
                    "admin_id": admin.id,
                    "admin_username": admin.username,
                    "note": payload.note or "",
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
            transaction_no=transaction_no,
            paid_at=timestamp,
        ),
    )
    receipt = receipt_service.generate_receipt_pdf(db, payment, receipt_type_for_payment(payment_type))
    log_activity(
        db,
        entity_type="payment",
        entity_id=payment.id,
        action="manual_payment_completed",
        admin_id=admin.id,
        new_values={
            "student_id": student.id,
            "application_id": application.id,
            "payment_type": payment_type,
            "amount": amount,
            "transaction_no": transaction_no,
            "receipt_id": receipt.id,
            "note": payload.note,
        },
    )
    db.commit()
    return receipt


@router.get("/admin/hostel/rooms")
@router.get("/api/admin/hostel/rooms")
def frontend_admin_rooms(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    return list_admin_rooms(db)


@router.post("/admin/merit/bulk-shortlist")
@router.post("/api/admin/merit/bulk-shortlist")
def frontend_bulk_shortlist_students(
    payload: FrontendBulkShortlistRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    student_ids = sorted({int(student_id) for student_id in payload.student_ids if student_id})
    if not student_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Select at least one student.")
    updated = 0
    failed: list[dict[str, Any]] = []
    for student_id in student_ids:
        application = crud.get_latest_student_application(db, student_id)
        if not application:
            failed.append({"student_id": student_id, "reason": "Application not found."})
            continue
        if payload.allotted_category:
            application.allotted_category = clean_text(payload.allotted_category)[:20]
        application.application_status = "Shortlisted"
        application.status = "Shortlisted"
        application.allocation_status = application.allocation_status or "pending"
        updated += 1
    db.commit()
    return {"updated": updated, "failed": failed}


@router.post("/admin/merit/room-allocation/preview")
@router.post("/api/admin/merit/room-allocation/preview")
def frontend_preview_room_allocation(
    payload: FrontendRoomAllocationImportRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    if not payload.rows:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Upload at least one allocation row.")
    return validate_room_allocation_rows(db, payload.rows)


@router.post("/admin/merit/room-allocation/import")
@router.post("/api/admin/merit/room-allocation/import")
def frontend_import_room_allocation(
    payload: FrontendRoomAllocationImportRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    validation = validate_room_allocation_rows(db, payload.rows)
    imported = 0
    for item in validation["items"]:
        if item["status"] != "valid":
            continue
        application = crud.get_application(db, int(item["application_db_id"]))
        if not application:
            item["status"] = "failed"
            item["message"] = "Application not found during import."
            continue
        try:
            crud.assign_application_bed(
                db,
                application,
                room_id=int(item["room_id"]),
                bed=item["bed_number"],
                allocation_status="allocated",
            )
            application.application_status = "Room Allocated"
            application.status = "Room Allocated"
            imported += 1
        except HTTPException as exc:
            item["status"] = "failed"
            item["message"] = str(exc.detail)
    db.commit()
    validation["summary"]["imported"] = imported
    validation["summary"]["failed"] = sum(1 for item in validation["items"] if item["status"] == "failed")
    validation["summary"]["skipped"] = sum(1 for item in validation["items"] if item["status"] == "skipped")
    return validation


@router.post("/admin/merit/publish")
@router.post("/api/admin/merit/publish")
def frontend_publish_merit(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    published = 0
    room_allocated = 0
    for application in crud.list_applications(db):
        current = (application.application_status or "").lower()
        if current in {"shortlisted", "published", "selected", "room allocated"}:
            if application.room_id and application.bed:
                application.application_status = "Room Allocated"
                application.status = "Room Allocated"
                application.allocation_status = "allocated"
                room_allocated += 1
            else:
                application.application_status = "Published"
                application.status = "Published"
                application.allocation_status = application.allocation_status or "pending"
                published += 1
    db.commit()
    return {"published": published, "room_allocated": room_allocated}


@router.patch("/admin/students/{student_id}/verify")
@router.patch("/api/admin/students/{student_id}/verify")
def frontend_verify_student(
    student_id: int,
    payload: FrontendVerifyRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    application = crud.get_latest_student_application(db, student_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    application.application_status = "Verified" if payload.verified else "Submitted"
    application.status = application.application_status
    db.commit()
    db.refresh(application)
    student = crud.get_student(db, student_id)
    return serialize_admin_student(student, application) if student else {"status": "ok"}


@router.patch("/admin/students/{student_id}/shortlist")
@router.patch("/api/admin/students/{student_id}/shortlist")
def frontend_shortlist_student(
    student_id: int,
    payload: FrontendShortlistRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    application = crud.get_latest_student_application(db, student_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    if payload.allotted_category:
        application.allotted_category = clean_text(payload.allotted_category)[:20]
    application.application_status = "Shortlisted" if payload.shortlisted else "Verified"
    application.status = application.application_status
    if not payload.shortlisted:
        application.allotted_category = None
        application.hostel_id = None
        application.room_id = None
        application.block = None
        application.floor = None
        application.bed = None
        application.allocation_status = "pending"
    db.commit()
    db.refresh(application)
    student = crud.get_student(db, student_id)
    return serialize_admin_student(student, application) if student else {"status": "ok"}


@router.patch("/admin/students/{student_id}/allocate-hostel")
@router.patch("/api/admin/students/{student_id}/allocate-hostel")
def frontend_allocate_hostel(
    student_id: int,
    payload: FrontendAllocateHostelRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_write_admin(authorization, db)
    application = crud.get_latest_student_application(db, student_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    hostel = None
    if payload.hostel_name:
        hostel = db.scalar(select(models.Hostel).where(models.Hostel.name == payload.hostel_name))
        if not hostel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hostel not found.")
        application.hostel_id = hostel.id
    room = None
    if payload.room_id:
        room = crud.get_room(db, payload.room_id)
    elif payload.room_number and application.hostel_id:
        room = db.scalar(
            select(models.Room).where(
                models.Room.hostel_id == application.hostel_id,
                models.Room.room_number == payload.room_number,
            )
        )
    if room and payload.bed_number:
        crud.assign_application_bed(
            db,
            application,
            room_id=room.id,
            bed=payload.bed_number,
            allocation_status="allocated",
        )
        application.application_status = "Room Allocated"
        application.status = "Room Allocated"
    elif room:
        application.hostel_id = room.hostel_id
        application.room_id = room.id
        application.application_status = "Published"
        application.status = "Published"
    elif application.hostel_id:
        application.application_status = "Published"
        application.status = "Published"
    db.commit()
    db.refresh(application)
    student = crud.get_student(db, student_id)
    return serialize_admin_student(student, application) if student else {"status": "ok"}


@router.post("/admin/students/{student_id}/account", response_model=schemas.AccountActionResponse)
@router.post("/api/admin/students/{student_id}/account", response_model=schemas.AccountActionResponse)
@router.patch("/admin/students/{student_id}/account", response_model=schemas.AccountActionResponse)
@router.patch("/api/admin/students/{student_id}/account", response_model=schemas.AccountActionResponse)
def frontend_update_student_account(
    student_id: int,
    payload: schemas.AdminStudentAccountUpdate,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    admin = require_write_admin(authorization, db)
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    application = crud.get_latest_student_application(db, student.id)

    student_code = clean_text(payload.application_number or payload.student_code)
    mobile = normalize_mobile(payload.mobile_number or payload.mobile)
    aadhaar = normalize_aadhaar(payload.aadhaar_number or payload.aadhar_number)
    course = clean_text(payload.course_name or payload.course)
    email = str(payload.email).strip().lower() if payload.email else None
    release_existing_deleted_conflicts(db, email=email, mobile=mobile)

    duplicate_checks = []
    if student_code and student_code != student.student_code:
        duplicate_checks.append(models.Student.student_code == student_code)
    if email and email != student.email:
        duplicate_checks.append(models.Student.email == email)
    if mobile and mobile != student.mobile:
        duplicate_checks.append(models.Student.mobile == mobile)
    if duplicate_checks:
        existing = db.scalar(select(models.Student).where(models.Student.id != student.id, or_(*duplicate_checks)))
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Student ID, email, or mobile number is already used by another student.",
            )

    old_values = {
        "student_code": student.student_code,
        "name": student.name,
        "email": student.email,
        "mobile": student.mobile,
        "is_active": student.is_active,
        "force_password_change": student.force_password_change,
        "aadhar_number": application.aadhar_number if application else None,
        "course": application.course if application else student.course,
        "session": application.session if application else student.session,
    }

    if student_code:
        student.student_code = student_code
    if payload.name is not None:
        student.name = clean_text(payload.name) or student.name
    if email:
        student.email = email
    if mobile:
        student.mobile = mobile
    if course is not None:
        student.course = course
    if payload.session is not None:
        student.session = clean_text(payload.session)
    if payload.is_active is not None:
        student.is_active = payload.is_active
    if payload.force_password_change is not None:
        student.force_password_change = payload.force_password_change

    if application:
        if student.name:
            pass
        if email:
            pass
        if mobile:
            pass
        if aadhaar:
            application.aadhar_number = aadhaar
        if course is not None:
            application.course = course
        if payload.session is not None:
            application.session = clean_text(payload.session)
        db.add(application)

    db.add(student)
    log_activity(
        db,
        entity_type="student",
        entity_id=student.id,
        action="account_update",
        admin_id=admin.id,
        old_values=old_values,
        new_values={
            "student_code": student.student_code,
            "name": student.name,
            "email": student.email,
            "mobile": student.mobile,
            "is_active": student.is_active,
            "force_password_change": student.force_password_change,
            "aadhar_number": application.aadhar_number if application else None,
            "course": application.course if application else student.course,
            "session": application.session if application else student.session,
        },
    )
    db.commit()
    db.refresh(student)
    if application:
        db.refresh(application)
    return schemas.AccountActionResponse(
        message="Account Updated Successfully",
        student=serialize_admin_student(student, application),
    )


@router.post("/admin/students/{student_id}/reset-password", response_model=schemas.AccountActionResponse)
@router.post("/api/admin/students/{student_id}/reset-password", response_model=schemas.AccountActionResponse)
def frontend_admin_reset_student_password(
    student_id: int,
    payload: schemas.AdminStudentPasswordReset,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    admin = require_write_admin(authorization, db)
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    password = crud.generate_temporary_password() if payload.generate_temporary or not payload.password else payload.password
    password_error = crud.validate_password_strength(password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=password_error)
    student.password_hash = crud.hash_password(password)
    student.force_password_change = payload.force_password_change
    student.reset_token_hash = None
    student.reset_token_expires_at = None
    student.reset_attempt_count = 0
    student.reset_last_attempt_at = None
    db.add(student)
    email_status = None
    if payload.send_email:
        email_status = send_account_email(
            student.email,
            "Student password reset",
            f"Your temporary password is {password}.",
        )
    log_activity(
        db,
        entity_type="student",
        entity_id=student.id,
        action="admin_password_reset",
        admin_id=admin.id,
        new_values={"force_password_change": student.force_password_change, "email_status": email_status},
    )
    db.commit()
    application = crud.get_latest_student_application(db, student.id)
    return schemas.AccountActionResponse(
        message="Password Reset Successfully",
        temporary_password=password if payload.generate_temporary else None,
        email_status=email_status,
        student=serialize_admin_student(student, application),
    )


@router.delete("/admin/students/{student_id}")
@router.delete("/api/admin/students/{student_id}")
def frontend_delete_student(
    student_id: int,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    admin = require_write_admin(authorization, db)
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    old_values = {
        "student_code": student.student_code,
        "email": student.email,
        "mobile": student.mobile,
        "is_active": student.is_active,
    }
    student.is_active = False
    student.password_hash = None
    release_deleted_student_unique_fields(db, student)
    log_activity(
        db,
        entity_type="student",
        entity_id=student.id,
        action="account_deactivated",
        admin_id=admin.id,
        old_values=old_values,
        new_values={
            "student_code": student.student_code,
            "email": student.email,
            "mobile": student.mobile,
            "is_active": False,
        },
    )
    db.commit()
    return {"status": "deactivated", "message": "Student ID deleted successfully. Login access has been revoked and email/mobile can be reused."}
