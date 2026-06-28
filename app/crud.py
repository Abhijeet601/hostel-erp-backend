import hashlib
import hmac
import secrets
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models, schemas


PASSWORD_HASH_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, stored_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, stored_digest)


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
    student.password_hash = hash_password(password)
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
    if not student or not student.password_hash or not verify_password(password, student.password_hash):
        return None
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


def create_room(db: Session, payload: schemas.RoomCreate) -> models.Room:
    room = models.Room(**payload.model_dump())
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def list_rooms(db: Session, hostel_id: int | None = None) -> list[models.Room]:
    stmt = select(models.Room).order_by(models.Room.floor, models.Room.room_number)
    if hostel_id:
        stmt = stmt.where(models.Room.hostel_id == hostel_id)
    return list(db.scalars(stmt))


def get_room(db: Session, room_id: int) -> models.Room | None:
    return db.get(models.Room, room_id)


def update_room(db: Session, room: models.Room, payload: schemas.RoomUpdate) -> models.Room:
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(room, key, value)
    db.commit()
    db.refresh(room)
    return room


def create_application(db: Session, payload: schemas.ApplicationCreate) -> models.HostelApplication:
    application = models.HostelApplication(**payload.model_dump())
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


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


def update_application_status(
    db: Session,
    application: models.HostelApplication,
    payload: schemas.ApplicationStatusUpdate,
) -> models.HostelApplication:
    old_room_id = application.room_id
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(application, key, value)
    if old_room_id and old_room_id != application.room_id:
        old_room = db.get(models.Room, old_room_id)
        if old_room:
            old_room.status = "available"
    if application.room_id:
        new_room = db.get(models.Room, application.room_id)
        if new_room:
            new_room.status = "occupied"
    db.commit()
    db.refresh(application)
    return application


def create_payment(db: Session, payload: schemas.PaymentCreate) -> models.Payment:
    payment = models.Payment(**payload.model_dump())
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


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
    return admin
