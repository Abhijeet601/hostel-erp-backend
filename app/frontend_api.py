"""Frontend compatibility routes for the static mmc-erp portal and React ERP client."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, models, receipt_service, schemas
from app.database import get_db


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
    email: str | None = None
    username: str | None = None
    password: str = Field(..., min_length=1, max_length=128)


class FrontendPaymentRequest(BaseModel):
    transaction_id: str | None = None
    amount: Decimal | None = None
    mode: str = "Demo"


class FrontendVerifyRequest(BaseModel):
    verified: bool = True


class FrontendShortlistRequest(BaseModel):
    shortlisted: bool = True


class FrontendAllocateHostelRequest(BaseModel):
    hostel_name: str | None = None
    room_id: int | None = None
    room_number: str | None = None


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
    if not student:
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
        },
    }


def serialize_admin_login(admin: models.AdminUser) -> dict[str, Any]:
    return {
        "role": admin.role,
        "access_token": admin_token(admin.id),
        "token": admin_token(admin.id),
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
        if lowered in {"shortlisted", "selected"}:
            shortlist_status = "shortlisted"
    hostel_name = application.hostel.name if application and application.hostel else None
    room_number = application.room.room_number if application and application.room else None
    return {
        "id": student.id,
        "application_number": student.student_code,
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
        "application_id": application.id if application else None,
    }


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
    application = crud.get_latest_student_application(db, student.id)
    payments = crud.list_payments(db, student_id=student.id)
    receipts = crud.list_receipts(db, student_id=student.id)
    registration_payment = next(
        (payment for payment in payments if "registration" in (payment.payment_type or "").lower()),
        None,
    )
    hostel_payment = next(
        (payment for payment in payments if "hostel" in (payment.payment_type or "").lower()),
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
    shortlisted = bool(application and (application.application_status or "").lower() in {"shortlisted", "selected", "approved"})
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
        "application_payment_status": "paid" if registration_payment else "pending",
        "hostel_receipt": bool(hostel_receipt),
        "allocated_hostel": application.hostel.name if application and application.hostel else None,
        "preferred_hostel": application.hostel.name if application and application.hostel else None,
        "room_number": application.room.room_number if application and application.room else None,
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
    pending_applications = 0
    for application in applications:
        category = application.applied_category or "Unknown"
        course = application.course or "Unknown"
        by_category[category] = by_category.get(category, 0) + 1
        by_course[course] = by_course.get(course, 0) + 1
        status_value = (application.application_status or "").lower()
        if status_value in {"verified", "approved", "selected"}:
            verified_students += 1
        if status_value in {"submitted", "pending", "draft"}:
            pending_applications += 1
    return {
        "total_applications": len(applications),
        "verified_students": verified_students,
        "pending_applications": pending_applications,
        "application_revenue": float(application_revenue),
        "hostel_revenue": float(hostel_revenue),
        "occupied_beds": occupied_beds,
        "available_beds": max(total_beds - occupied_beds, 0),
        "by_category": [{"label": key, "value": value} for key, value in sorted(by_category.items())],
        "by_course": [{"label": key, "value": value} for key, value in sorted(by_course.items())],
    }


def list_admin_rooms(db: Session) -> dict[str, list[dict[str, Any]]]:
    rooms = crud.list_rooms(db)
    occupied_counts: dict[int, int] = {}
    for application in crud.list_applications(db):
        if application.room_id:
            occupied_counts[application.room_id] = occupied_counts.get(application.room_id, 0) + 1
    items = []
    for room in rooms:
        hostel = crud.get_hostel(db, room.hostel_id)
        occupied = occupied_counts.get(room.id, 0)
        if room.status == "occupied" and occupied == 0:
            occupied = room.beds
        available_beds = max(room.beds - occupied, 0)
        items.append(
            {
                "id": room.id,
                "hostel_name": hostel.name if hostel else "",
                "room_number": room.room_number,
                "block_name": str(room.floor),
                "bed_capacity": room.beds,
                "available_beds": available_beds,
                "status": "available" if available_beds > 0 else "occupied",
            }
        )
    return {"items": items}


def resolve_login_identifier(payload: FrontendLoginRequest) -> str:
    identifier = payload.identifier or payload.email or payload.username or ""
    return identifier.strip()


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


def register_student_compat(payload: FrontendRegisterRequest, db: Session) -> dict[str, Any]:
    from app.main import save_or_409

    mobile = (payload.mobile_number or payload.mobile or "").strip()
    if len(mobile) > 10:
        mobile = mobile[-10:]
    name = (payload.name or payload.email.split("@")[0]).strip()
    register_payload = schemas.StudentRegister(
        name=name,
        email=payload.email,
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
        if application.application_status != "Draft":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Application has already been submitted.")
        require_admission_open(db, existing_draft=False)
        for step in range(1, 9):
            merged = {field: getattr(application, field, None) for field in crud.APPLICATION_DRAFT_FIELDS}
            merged.update(raw_data)
            validate_step_payload(step, merged)
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
    saved = crud.save_application_draft(db, student, current_step, raw_data)
    return serialize_application_form(student, saved)


def demo_payment(
    student: models.Student,
    db: Session,
    *,
    payment_kind: str,
    transaction_id: str | None,
) -> dict[str, Any]:
    from app.main import require_payment_open, save_or_409

    require_payment_open(db)
    application = crud.get_latest_student_application(db, student.id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submit your hostel application before payment.")
    if payment_kind == "registration":
        if (application.application_status or "").lower() == "draft":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submit your application before paying registration fee.")
        expected = Decimal("100") if application.application_type == "existing" else Decimal("1000")
        payment_type = "Registration Fee"
        existing = crud.get_successful_payment_for_application(db, application.id, payment_type)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Registration fee has already been paid.")
        receipt_type = "application_registration"
    else:
        if (application.application_status or "").lower() not in {"shortlisted", "selected", "approved"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Hostel fee requires shortlisting.")
        if not application.hostel:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A hostel must be allotted first.")
        expected = application.hostel.fee if application.hostel.fee and application.hostel.fee > 0 else Decimal("10000")
        payment_type = "Hostel Admission Fee"
        existing = crud.get_successful_payment_for_application(db, application.id, payment_type)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Hostel fee has already been paid.")
        receipt_type = "hostel_admission"
    payment_payload = schemas.PaymentCreate(
        student_id=student.id,
        application_id=application.id,
        payment_type=payment_type,
        amount=expected,
        mode="Demo",
        status="Paid",
        transaction_no=transaction_id or f"DEMO-{payment_kind.upper()}-{int(datetime.now(timezone.utc).timestamp())}",
        paid_at=datetime.now(timezone.utc),
    )
    payment = save_or_409(lambda: crud.create_payment(db, payment_payload))
    receipt = receipt_service.generate_receipt_pdf(db, payment, receipt_type)
    return {
        "id": payment.id,
        "payment_type": payment.payment_type,
        "amount": float(payment.amount),
        "status": payment.status,
        "receipt_url": receipt.pdf_url or f"/receipts/{receipt.id}/download",
        "receipt": receipt,
    }


@router.post("/register")
@router.post("/api/register")
def frontend_register(payload: FrontendRegisterRequest, db: Session = Depends(get_db)):
    return register_student_compat(payload, db)


@router.post("/login")
@router.post("/api/login")
def frontend_login(payload: FrontendLoginRequest, db: Session = Depends(get_db)):
    return login_student_or_admin(payload, db)


@router.post("/api/admin/login")
@router.post("/api/auth/admin/login")
def frontend_admin_login(payload: FrontendAdminLoginRequest, db: Session = Depends(get_db)):
    identifier = (payload.username or payload.email or "").strip()
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
    student = require_student(authorization, db)
    return demo_payment(student, db, payment_kind="registration", transaction_id=payload.transaction_id)


@router.post("/payment/hostel")
@router.post("/api/payment/hostel")
def frontend_payment_hostel(
    payload: FrontendPaymentRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    student = require_student(authorization, db)
    return demo_payment(student, db, payment_kind="hostel", transaction_id=payload.transaction_id)


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
    students = crud.list_students(db, limit=limit)
    items = []
    for student in students:
        application = crud.get_latest_student_application(db, student.id)
        items.append(serialize_admin_student(student, application))
    return {"items": items}


@router.get("/admin/payments")
@router.get("/api/admin/payments")
def frontend_admin_payments(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    payments = crud.list_payments(db)
    return {"items": payments}


@router.get("/admin/hostel/rooms")
@router.get("/api/admin/hostel/rooms")
def frontend_admin_rooms(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    return list_admin_rooms(db)


@router.patch("/admin/students/{student_id}/verify")
@router.patch("/api/admin/students/{student_id}/verify")
def frontend_verify_student(
    student_id: int,
    payload: FrontendVerifyRequest,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
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
    require_admin(authorization, db)
    application = crud.get_latest_student_application(db, student_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    application.application_status = "Shortlisted" if payload.shortlisted else "Verified"
    application.status = application.application_status
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
    require_admin(authorization, db)
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
    if room:
        application.room_id = room.id
    application.application_status = "Selected"
    application.status = "Selected"
    db.commit()
    db.refresh(application)
    student = crud.get_student(db, student_id)
    return serialize_admin_student(student, application) if student else {"status": "ok"}


@router.delete("/admin/students/{student_id}")
@router.delete("/api/admin/students/{student_id}")
def frontend_delete_student(
    student_id: int,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    db.delete(student)
    db.commit()
    return {"status": "deleted"}
