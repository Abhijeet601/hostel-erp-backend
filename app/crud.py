import hashlib
import hmac
import secrets
import string
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import models, schemas


PASSWORD_HASH_ITERATIONS = 260_000
try:
    from passlib.context import CryptContext

    legacy_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
except Exception:
    legacy_pwd_context = None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def validate_password_strength(password: str, confirm_password: str | None = None) -> str | None:
    if confirm_password is not None and password != confirm_password:
        return "New password and confirm password do not match."
    if len(password or "") < 8:
        return "Password must be at least 8 characters long."
    if not any(char.isupper() for char in password):
        return "Password must include at least one uppercase letter."
    if not any(char.islower() for char in password):
        return "Password must include at least one lowercase letter."
    if not any(char.isdigit() for char in password):
        return "Password must include at least one number."
    if not any(char in string.punctuation for char in password):
        return "Password must include at least one special character."
    return None


def generate_temporary_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(max(length, 10)))
        if validate_password_strength(password) is None:
            return password


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations, salt, stored_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            raise ValueError
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return hmac.compare_digest(digest, stored_digest)
    except (ValueError, TypeError):
        if password_hash.startswith(("$2a$", "$2b$", "$2y$")) and legacy_pwd_context is not None:
            try:
                return bool(legacy_pwd_context.verify(password, password_hash))
            except Exception:
                return False
        if "$" not in password_hash and len(password_hash) < 128:
            return hmac.compare_digest(password, password_hash)
        return False


def needs_password_rehash(password_hash: str | None) -> bool:
    return not bool(password_hash and password_hash.startswith("pbkdf2_sha256$"))


def create_student(db: Session, payload: schemas.StudentCreate) -> models.Student:
    student = models.Student(**payload.model_dump())
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def generate_student_code(db: Session) -> str:
    prefix = f"MMC{datetime.now().year}"
    latest = db.scalar(
        select(models.Student.student_code)
        .where(models.Student.student_code.like(f"{prefix}%"))
        .order_by(models.Student.id.desc())
        .limit(1)
    )
    next_number = 1
    if latest and latest.startswith(prefix):
        try:
            next_number = int(latest.removeprefix(prefix)) + 1
        except ValueError:
            next_number = 1
    return f"{prefix}{next_number:05d}"


def register_student(db: Session, payload: schemas.StudentRegister) -> models.Student:
    password_error = validate_password_strength(payload.password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=password_error)
    student = models.Student(
        student_code=generate_student_code(db),
        name=payload.name,
        email=payload.email,
        mobile=payload.mobile,
        date_of_birth=payload.date_of_birth,
        password_hash=hash_password(payload.password),
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def list_students(db: Session, skip: int = 0, limit: int = 100) -> list[models.Student]:
    return list(db.scalars(select(models.Student).offset(skip).limit(limit)))


def get_student(db: Session, student_id: int) -> models.Student | None:
    return db.get(models.Student, student_id)


def update_student(db: Session, student: models.Student, payload: schemas.StudentUpdate) -> models.Student:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(student, key, value)
    db.commit()
    db.refresh(student)
    return student


def update_student_password(db: Session, student: models.Student, password: str) -> models.Student:
    password_error = validate_password_strength(password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=password_error)
    student.password_hash = hash_password(password)
    student.force_password_change = False
    db.commit()
    db.refresh(student)
    return student


def authenticate_student(db: Session, identifier: str, password: str) -> models.Student | None:
    stmt = select(models.Student).where(
        or_(
            models.Student.student_code == identifier,
            models.Student.email == identifier,
            models.Student.mobile == identifier,
        )
    )
    student = db.scalar(stmt)
    if not student or not student.is_active or not student.password_hash or not verify_password(password, student.password_hash):
        return None
    if needs_password_rehash(student.password_hash):
        student.password_hash = hash_password(password)
        db.add(student)
        db.commit()
        db.refresh(student)
    return student


def create_hostel(db: Session, payload: schemas.HostelCreate) -> models.Hostel:
    hostel = models.Hostel(**payload.model_dump())
    db.add(hostel)
    db.commit()
    db.refresh(hostel)
    return hostel


def list_hostels(db: Session) -> list[models.Hostel]:
    return list(db.scalars(select(models.Hostel).order_by(models.Hostel.name)))


def get_hostel(db: Session, hostel_id: int) -> models.Hostel | None:
    return db.get(models.Hostel, hostel_id)


def update_hostel(db: Session, hostel: models.Hostel, payload: schemas.HostelUpdate) -> models.Hostel:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(hostel, key, value)
    db.commit()
    db.refresh(hostel)
    return hostel


def normalize_bed_value(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower().replace("bed ", "")
    mapping = {"a": "A", "b": "B", "c": "C", "1": "A", "2": "B", "3": "C"}
    return mapping.get(normalized, normalized.upper())


def normalize_allocation_payload(payload: dict | None) -> dict:
    if not payload:
        return {}
    normalized = dict(payload)
    normalized["bed"] = normalize_bed_value(normalized.get("bed"))
    if normalized.get("allocation_status") in {None, ""}:
        normalized["allocation_status"] = "allocated"
    return normalized


def create_room(db: Session, payload: schemas.RoomCreate) -> models.Room:
    room = models.Room(**payload.model_dump())
    room.available_beds = max(int(room.beds or 0), 0)
    room.occupied_beds = 0
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def list_rooms(db: Session, hostel_id: int | None = None) -> list[models.Room]:
    stmt = select(models.Room).order_by(models.Room.floor, models.Room.room_number)
    if hostel_id:
        stmt = stmt.where(models.Room.hostel_id == hostel_id)
    rooms = list(db.scalars(stmt))
    for room in rooms:
        sync_room_occupancy(db, room)
    if rooms:
        db.commit()
    return rooms


def get_room(db: Session, room_id: int) -> models.Room | None:
    return db.get(models.Room, room_id)


def update_room(db: Session, room: models.Room, payload: schemas.RoomUpdate) -> models.Room:
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(room, key, value)
    if "beds" in values or "occupied_beds" in values or "available_beds" in values:
        total_beds = max(int(room.beds or 0), 0)
        occupied_beds = min(int(room.occupied_beds or 0), total_beds)
        available_beds = max(total_beds - occupied_beds, 0)
        room.occupied_beds = occupied_beds
        room.available_beds = available_beds
    elif room.available_beds is None:
        room.available_beds = max(int(room.beds or 0), 0)
    db.commit()
    db.refresh(room)
    return room


def sync_room_occupancy(db: Session, room: models.Room) -> models.Room:
    if not room:
        return room
    total_beds = max(int(room.beds or 0), 0)
    occupied_rows = db.execute(
        select(models.HostelApplication)
        .where(
            models.HostelApplication.room_id == room.id,
            models.HostelApplication.allocation_status != "vacated",
            models.HostelApplication.bed.is_not(None),
            models.HostelApplication.application_status != "Draft",
        )
    ).scalars().all()
    occupied_beds = len(occupied_rows)
    room.occupied_beds = min(occupied_beds, total_beds)
    room.available_beds = max(total_beds - room.occupied_beds, 0)
    if room.status not in {"reserved", "maintenance"}:
        room.status = "occupied" if room.occupied_beds >= total_beds else "available"
    return room


def create_application(db: Session, payload: schemas.ApplicationCreate) -> models.HostelApplication:
    values = payload.model_dump()
    values.setdefault("status", "Submitted")
    values.setdefault("application_status", values["status"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    values.setdefault("submitted_at", now)
    values.setdefault("last_saved_at", now)
    values.setdefault("current_step", 8)
    values = normalize_allocation_payload(values)
    application = models.HostelApplication(**values)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def generate_application_no(student_id: int) -> str:
    return f"MMC-HST-{datetime.now().year}-{student_id}-{datetime.now().strftime('%H%M%S%f')[-8:]}"


def list_applications(
    db: Session,
    status: str | None = None,
    student_id: int | None = None,
) -> list[models.HostelApplication]:
    stmt = select(models.HostelApplication).order_by(models.HostelApplication.created_at.desc())
    if status:
        stmt = stmt.where(models.HostelApplication.status == status)
    if student_id:
        stmt = stmt.where(models.HostelApplication.student_id == student_id)
    return list(db.scalars(stmt))


def get_application(db: Session, application_id: int) -> models.HostelApplication | None:
    return db.get(models.HostelApplication, application_id)


def get_student_application_for_session(
    db: Session,
    student_id: int,
    session: str | None,
) -> models.HostelApplication | None:
    if not session:
        return None
    return db.scalar(
        select(models.HostelApplication).where(
            models.HostelApplication.student_id == student_id,
            models.HostelApplication.session == session,
        )
    )


def get_editable_student_application(db: Session, student_id: int) -> models.HostelApplication | None:
    return db.scalar(
        select(models.HostelApplication)
        .where(
            models.HostelApplication.student_id == student_id,
            models.HostelApplication.application_status == "Draft",
        )
        .order_by(models.HostelApplication.updated_at.desc(), models.HostelApplication.id.desc())
        .limit(1)
    )


def get_latest_student_application(db: Session, student_id: int) -> models.HostelApplication | None:
    return db.scalar(
        select(models.HostelApplication)
        .where(models.HostelApplication.student_id == student_id)
        .order_by(models.HostelApplication.updated_at.desc(), models.HostelApplication.id.desc())
        .limit(1)
    )


APPLICATION_DRAFT_FIELDS = {
    "application_type",
    "admission_level",
    "admission_id",
    "college_name",
    "course",
    "session",
    "father_name",
    "mother_name",
    "guardian_name",
    "guardian_mobile",
    "permanent_address",
    "correspondence_address",
    "blood_group",
    "aadhar_number",
    "religion",
    "nationality",
    "student_photo_data",
    "aadhar_card_data",
    "admission_receipt_data",
    "income_certificate_data",
    "caste_certificate_data",
    "intermediate_college",
    "board",
    "previous_course",
    "result_type",
    "marks_obtained",
    "total_marks",
    "percentage",
    "roll_number",
    "subject",
    "applied_category",
    "allotted_category",
    "hostel_id",
    "room_id",
    "block",
    "floor",
    "bed",
    "allocation_date",
    "allocation_status",
}

STUDENT_DRAFT_FIELDS = {
    "name",
    "email",
    "mobile",
    "date_of_birth",
    "gender",
    "category",
    "course",
    "session",
}


def save_application_draft(
    db: Session,
    student: models.Student,
    current_step: int,
    data: dict,
) -> models.HostelApplication:
    application = get_editable_student_application(db, student.id)
    if not application:
        application = models.HostelApplication(
            application_no=generate_application_no(student.id),
            student_id=student.id,
            status="Draft",
            application_status="Draft",
            current_step=current_step,
        )
        db.add(application)

    for key, value in data.items():
        if key in APPLICATION_DRAFT_FIELDS:
            setattr(application, key, value)
        if key in STUDENT_DRAFT_FIELDS:
            setattr(student, key, value)
    application.bed = normalize_bed_value(application.bed)

    application.current_step = max(application.current_step or 1, current_step)
    application.status = "Draft"
    application.application_status = "Draft"
    application.last_saved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(application)
    return application


def submit_application(db: Session, application: models.HostelApplication, data: dict | None = None) -> models.HostelApplication:
    if data:
        for key, value in data.items():
            if key in APPLICATION_DRAFT_FIELDS:
                setattr(application, key, value)
    application.bed = normalize_bed_value(application.bed)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    application.current_step = 8
    application.status = "Submitted"
    application.application_status = "Submitted"
    application.last_saved_at = now
    application.submitted_at = now
    if application.room_id and application.bed:
        assign_application_bed(db, application)
    else:
        application.allocation_status = "pending"
    db.commit()
    db.refresh(application)
    return application


def assign_application_bed(
    db: Session,
    application: models.HostelApplication,
    room_id: int | None = None,
    bed: str | None = None,
    hostel_id: int | None = None,
    block: str | None = None,
    floor: str | None = None,
    allocation_date: datetime | None = None,
    allocation_status: str | None = None,
) -> models.HostelApplication:
    if room_id is None:
        room_id = application.room_id
    if bed is None:
        bed = application.bed
    if hostel_id is None:
        hostel_id = application.hostel_id
    if block is None:
        block = application.block
    if floor is None:
        floor = application.floor
    if allocation_date is None:
        allocation_date = application.allocation_date
    if allocation_status is None:
        allocation_status = application.allocation_status or "allocated"
    if not room_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Room is required for allocation.")
    if not bed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Bed is required for allocation.")
    room = db.get(models.Room, room_id)
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    if hostel_id is not None and int(hostel_id) != int(room.hostel_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected room does not belong to the chosen hostel.")
    normalized_bed = normalize_bed_value(bed)
    if normalized_bed not in {"A", "B", "C"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Bed must be A, B, or C.")
    existing_conflict = db.scalar(
        select(models.HostelApplication).where(
            models.HostelApplication.room_id == room_id,
            models.HostelApplication.bed == normalized_bed,
            models.HostelApplication.allocation_status != "vacated",
            models.HostelApplication.application_status != "Draft",
            models.HostelApplication.id != application.id,
        )
    )
    if existing_conflict:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Selected bed is already occupied.")
    if room.occupied_beds >= max(int(room.beds or 0), 0) and (
        application.room_id != room_id or application.bed != normalized_bed
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room is fully occupied. Please select another room.")

    old_room_id = application.room_id
    if old_room_id and old_room_id != room_id:
        old_room = db.get(models.Room, old_room_id)
        if old_room:
            sync_room_occupancy(db, old_room)
    application.hostel_id = hostel_id
    application.room_id = room_id
    application.block = block
    application.floor = floor
    application.bed = normalized_bed
    application.allocation_date = allocation_date or application.allocation_date or datetime.now(timezone.utc).date()
    application.allocation_status = allocation_status or "allocated"
    sync_room_occupancy(db, room)
    db.add(room)
    if old_room_id and old_room_id != room_id:
        old_room = db.get(models.Room, old_room_id)
        if old_room:
            sync_room_occupancy(db, old_room)
            db.add(old_room)
    db.add(application)
    return application


def release_application_bed(db: Session, application: models.HostelApplication) -> models.HostelApplication:
    old_room_id = application.room_id
    application.bed = None
    application.allocation_status = "vacated"
    db.add(application)
    if application.room_id:
        room = db.get(models.Room, old_room_id)
        if room:
            sync_room_occupancy(db, room)
            db.add(room)
    return application


def update_application_status(
    db: Session,
    application: models.HostelApplication,
    payload: schemas.ApplicationStatusUpdate,
) -> models.HostelApplication:
    data = payload.model_dump(exclude_unset=True)
    old_room_id = application.room_id
    for key, value in data.items():
        setattr(application, key, value)
        if key == "status":
            application.application_status = value
    application.bed = normalize_bed_value(application.bed)
    if data.get("room_id") or data.get("bed"):
        if data.get("room_id") and data.get("bed"):
            assign_application_bed(
                db,
                application,
                room_id=data.get("room_id"),
                bed=data.get("bed"),
                hostel_id=data.get("hostel_id"),
                block=data.get("block"),
                floor=data.get("floor"),
                allocation_date=data.get("allocation_date"),
                allocation_status=data.get("allocation_status"),
            )
        elif data.get("room_id") and not data.get("bed") and application.bed:
            assign_application_bed(
                db,
                application,
                room_id=data.get("room_id"),
                bed=application.bed,
                hostel_id=data.get("hostel_id"),
                block=data.get("block"),
                floor=data.get("floor"),
                allocation_date=data.get("allocation_date"),
                allocation_status=data.get("allocation_status"),
            )
    if data.get("allocation_status") == "vacated":
        release_application_bed(db, application)
    if old_room_id and old_room_id != application.room_id and not data.get("room_id"):
        old_room = db.get(models.Room, old_room_id)
        if old_room:
            sync_room_occupancy(db, old_room)
            db.add(old_room)
    if application.room_id and not data.get("room_id") and not data.get("bed"):
        room = db.get(models.Room, application.room_id)
        if room:
            sync_room_occupancy(db, room)
            db.add(room)
    db.commit()
    db.refresh(application)
    return application


def get_application_settings(db: Session) -> models.AdmissionPaymentSettings:
    settings = db.get(models.AdmissionPaymentSettings, 1)
    if settings:
        return settings
    settings = models.AdmissionPaymentSettings(id=1)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def update_application_settings(
    db: Session,
    payload: schemas.ApplicationSettingsUpdate,
) -> models.AdmissionPaymentSettings:
    settings = get_application_settings(db)
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    db.commit()
    db.refresh(settings)
    return settings


def count_applications_by_status(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(models.HostelApplication.application_status, func.count(models.HostelApplication.id))
        .group_by(models.HostelApplication.application_status)
    ).all()
    return {str(status or "Draft"): int(count) for status, count in rows}


def create_payment(db: Session, payload: schemas.PaymentCreate) -> models.Payment:
    payment = models.Payment(**payload.model_dump())
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def get_payment_by_transaction_no(db: Session, transaction_no: str) -> models.Payment | None:
    return db.scalar(select(models.Payment).where(models.Payment.transaction_no == transaction_no))


def get_pending_payment_for_application(
    db: Session,
    application_id: int,
    payment_type: str,
) -> models.Payment | None:
    return db.scalar(
        select(models.Payment)
        .where(
            models.Payment.application_id == application_id,
            models.Payment.payment_type == payment_type,
            models.Payment.status.in_(["Pending", "pending", "Initiated", "initiated"]),
        )
        .order_by(models.Payment.created_at.desc())
    )


def update_payment_gateway_result(
    db: Session,
    payment: models.Payment,
    *,
    transaction_no: str | None = None,
    mode: str | None = None,
    status: str | None = None,
    tracking_id: str | None = None,
    bank_ref_no: str | None = None,
    failure_reason: str | None = None,
    currency: str | None = None,
    sub_account_id: str | None = None,
    gateway_response: str | None = None,
    paid_at: datetime | None = None,
) -> models.Payment:
    if transaction_no:
        payment.transaction_no = transaction_no
    if mode:
        payment.mode = mode
    if status:
        payment.status = status
    if tracking_id is not None:
        payment.tracking_id = tracking_id
    if bank_ref_no is not None:
        payment.bank_ref_no = bank_ref_no
    if failure_reason is not None:
        payment.failure_reason = failure_reason
    if currency:
        payment.currency = currency
    if sub_account_id:
        payment.sub_account_id = sub_account_id
    if gateway_response is not None:
        payment.gateway_response = gateway_response
    if paid_at:
        payment.paid_at = paid_at
    db.commit()
    db.refresh(payment)
    return payment


def delete_demo_payment_data(db: Session) -> int:
    receipt_conditions = [models.PaymentReceipt.transaction_id.like("DEMO-%")]
    demo_payments = list(
        db.scalars(
            select(models.Payment).where(
                or_(
                    models.Payment.transaction_no.like("DEMO-%"),
                    func.lower(models.Payment.mode).like("%demo%"),
                )
            )
        )
    )
    demo_payment_ids = [payment.id for payment in demo_payments]
    if demo_payment_ids:
        receipt_conditions.append(models.PaymentReceipt.payment_id.in_(demo_payment_ids))
    demo_receipts = list(
        db.scalars(select(models.PaymentReceipt).where(or_(*receipt_conditions)))
    )
    for receipt in demo_receipts:
        db.delete(receipt)
    for payment in demo_payments:
        db.delete(payment)
    if demo_receipts or demo_payments:
        db.commit()
    return len(demo_receipts) + len(demo_payments)


def get_successful_payment_for_application(
    db: Session,
    application_id: int,
    payment_type: str,
) -> models.Payment | None:
    return db.scalar(
        select(models.Payment).where(
            models.Payment.application_id == application_id,
            models.Payment.payment_type == payment_type,
            models.Payment.status.in_(["Paid", "Success", "Completed", "paid", "success", "completed"]),
        )
    )


def list_payments(db: Session, student_id: int | None = None) -> list[models.Payment]:
    stmt = select(models.Payment).order_by(models.Payment.created_at.desc())
    if student_id:
        stmt = stmt.where(models.Payment.student_id == student_id)
    return list(db.scalars(stmt))


def get_payment(db: Session, payment_id: int) -> models.Payment | None:
    return db.get(models.Payment, payment_id)


def list_receipts(db: Session, student_id: int | None = None) -> list[models.PaymentReceipt]:
    stmt = select(models.PaymentReceipt).order_by(models.PaymentReceipt.generated_at.desc())
    if student_id:
        stmt = stmt.where(models.PaymentReceipt.student_id == student_id)
    return list(db.scalars(stmt))


def get_receipt(db: Session, receipt_id: int) -> models.PaymentReceipt | None:
    return db.get(models.PaymentReceipt, receipt_id)


def get_receipt_by_number(db: Session, receipt_number: str) -> models.PaymentReceipt | None:
    return db.scalar(select(models.PaymentReceipt).where(models.PaymentReceipt.receipt_number == receipt_number))


def create_admin(db: Session, payload: schemas.AdminCreate) -> models.AdminUser:
    admin = models.AdminUser(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def list_admins(db: Session) -> list[models.AdminUser]:
    return list(db.scalars(select(models.AdminUser).order_by(models.AdminUser.username)))


def authenticate_admin(db: Session, identifier: str, password: str) -> models.AdminUser | None:
    stmt = select(models.AdminUser).where(
        or_(models.AdminUser.username == identifier, models.AdminUser.email == identifier),
        models.AdminUser.is_active.is_(True),
    )
    admin = db.scalar(stmt)
    if not admin or not verify_password(password, admin.password_hash):
        return None
    if needs_password_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return admin


def ensure_default_admin(
    db: Session,
    *,
    username: str,
    email: str,
    password: str,
    full_name: str,
) -> models.AdminUser | None:
    existing_count = db.scalar(select(func.count(models.AdminUser.id))) or 0
    if existing_count:
        return None
    admin = models.AdminUser(
        username=username,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        role="super_admin",
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
