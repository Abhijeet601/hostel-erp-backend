import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from threading import Lock
from urllib.parse import parse_qs, urlencode

from Crypto.Cipher import AES
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session, joinedload

from app import crud, models, receipt_service, schemas
from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.document_storage import upload_application_documents
from app.frontend_api import router as frontend_router
from app.r2_storage import get_r2_service


settings = get_settings()
HOSTEL_CCAVENUE_SUB_ACCOUNT_ID = "MahimaHostel"
logger = logging.getLogger(__name__)
payment_initiation_locks: dict[tuple[int, str], Lock] = {}
payment_initiation_locks_guard = Lock()

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-ms"] = f"{elapsed_ms:.1f}"
    if elapsed_ms >= 750:
        logger.warning(
            "Slow request method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    return response

app.include_router(frontend_router)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates()
    ensure_database_indexes()
    ensure_default_admin()
    remove_demo_payment_data()


def remove_demo_payment_data() -> None:
    with SessionLocal() as db:
        crud.delete_demo_payment_data(db)


def ensure_default_admin() -> None:
    with SessionLocal() as db:
        crud.ensure_default_admin(
            db,
            username=settings.admin_username,
            email=settings.admin_email,
            password=settings.admin_password,
            full_name=settings.admin_full_name,
        )


def ensure_schema_updates() -> None:
    if engine.dialect.name != "mysql":
        required_student_columns = {
            "is_active": "BOOLEAN NOT NULL DEFAULT 1",
            "force_password_change": "BOOLEAN NOT NULL DEFAULT 0",
            "reset_token_hash": "VARCHAR(255)",
            "reset_token_expires_at": "DATETIME",
            "reset_requested_at": "DATETIME",
            "reset_attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "reset_last_attempt_at": "DATETIME",
        }
        required_application_columns = {
            "aadhar_card_data": "TEXT",
            "admission_receipt_data": "TEXT",
            "income_certificate_data": "TEXT",
            "caste_certificate_data": "TEXT",
            "existing_hostel_name": "VARCHAR(120)",
            "existing_room_number": "VARCHAR(40)",
            "existing_bed_number": "VARCHAR(40)",
            "existing_block": "VARCHAR(40)",
            "existing_floor": "VARCHAR(40)",
            "existing_previous_session": "VARCHAR(20)",
        }
        required_payment_columns = {
            "currency": "VARCHAR(10) NOT NULL DEFAULT 'INR'",
            "tracking_id": "VARCHAR(80)",
            "bank_ref_no": "VARCHAR(80)",
            "failure_reason": "VARCHAR(255)",
            "sub_account_id": "VARCHAR(80)",
            "gateway_response": "TEXT",
        }
        with engine.begin() as conn:
            student_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(students)"))}
            for column, ddl in required_student_columns.items():
                if column not in student_columns:
                    conn.execute(text(f"ALTER TABLE students ADD COLUMN {column} {ddl}"))
            application_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(hostel_applications)"))}
            for column, ddl in required_application_columns.items():
                if column not in application_columns:
                    conn.execute(text(f"ALTER TABLE hostel_applications ADD COLUMN {column} {ddl}"))
            payment_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(payments)"))}
            for column, ddl in required_payment_columns.items():
                if column not in payment_columns:
                    conn.execute(text(f"ALTER TABLE payments ADD COLUMN {column} {ddl}"))
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS activity_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        entity_type VARCHAR(50),
                        entity_id VARCHAR(50),
                        action VARCHAR(80),
                        old_values TEXT NULL,
                        new_values TEXT NULL,
                        admin_id INTEGER NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        return
    required_application_columns = {
        "application_status": "VARCHAR(30) NOT NULL DEFAULT 'Draft'",
        "current_step": "INT NOT NULL DEFAULT 1",
        "last_saved_at": "DATETIME NULL",
        "submitted_at": "DATETIME NULL",
        "block": "VARCHAR(40) NULL",
        "floor": "VARCHAR(20) NULL",
        "bed": "VARCHAR(20) NULL",
        "allocation_date": "DATE NULL",
        "allocation_status": "VARCHAR(30) NOT NULL DEFAULT 'allocated'",
        "aadhar_card_data": "LONGTEXT NULL",
        "admission_receipt_data": "LONGTEXT NULL",
        "income_certificate_data": "LONGTEXT NULL",
        "caste_certificate_data": "LONGTEXT NULL",
        "existing_hostel_name": "VARCHAR(120) NULL",
        "existing_room_number": "VARCHAR(40) NULL",
        "existing_bed_number": "VARCHAR(40) NULL",
        "existing_block": "VARCHAR(40) NULL",
        "existing_floor": "VARCHAR(40) NULL",
        "existing_previous_session": "VARCHAR(20) NULL",
    }
    required_room_columns = {
        "occupied_beds": "INT NOT NULL DEFAULT 0",
        "available_beds": "INT NOT NULL DEFAULT 3",
    }
    required_student_columns = {
        "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
        "force_password_change": "BOOLEAN NOT NULL DEFAULT FALSE",
        "reset_token_hash": "VARCHAR(255) NULL",
        "reset_token_expires_at": "DATETIME NULL",
        "reset_requested_at": "DATETIME NULL",
        "reset_attempt_count": "INT NOT NULL DEFAULT 0",
        "reset_last_attempt_at": "DATETIME NULL",
    }
    required_payment_columns = {
        "currency": "VARCHAR(10) NOT NULL DEFAULT 'INR'",
        "tracking_id": "VARCHAR(80) NULL",
        "bank_ref_no": "VARCHAR(80) NULL",
        "failure_reason": "VARCHAR(255) NULL",
        "sub_account_id": "VARCHAR(80) NULL",
        "gateway_response": "TEXT NULL",
    }
    with engine.begin() as conn:
        application_columns = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'hostel_applications'
                    """
                )
            )
        }
        room_columns = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'rooms'
                    """
                )
            )
        }
        payment_column_lengths = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    """
                    SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'payments'
                    """
                )
            )
        }
        payment_columns = set(payment_column_lengths)
        student_columns = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'students'
                    """
                )
            )
        }
        for column, ddl in required_application_columns.items():
            if column not in application_columns:
                conn.execute(text(f"ALTER TABLE hostel_applications ADD COLUMN {column} {ddl}"))
        for column, ddl in required_room_columns.items():
            if column not in room_columns:
                conn.execute(text(f"ALTER TABLE rooms ADD COLUMN {column} {ddl}"))
        for column, ddl in required_student_columns.items():
            if column not in student_columns:
                conn.execute(text(f"ALTER TABLE students ADD COLUMN {column} {ddl}"))
        for column, ddl in required_payment_columns.items():
            if column not in payment_columns:
                conn.execute(text(f"ALTER TABLE payments ADD COLUMN {column} {ddl}"))
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS activity_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    entity_type VARCHAR(50),
                    entity_id VARCHAR(50),
                    action VARCHAR(80),
                    old_values TEXT NULL,
                    new_values TEXT NULL,
                    admin_id INT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_activity_logs_entity_type (entity_type),
                    INDEX ix_activity_logs_entity_id (entity_id),
                    INDEX ix_activity_logs_action (action)
                )
                """
            )
        )
        conn.execute(text("ALTER TABLE hostel_applications MODIFY admission_id VARCHAR(50) NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY applied_category VARCHAR(20) NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY student_photo_data LONGTEXT NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY aadhar_card_data LONGTEXT NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY admission_receipt_data LONGTEXT NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY income_certificate_data LONGTEXT NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY caste_certificate_data LONGTEXT NULL"))
        if (payment_column_lengths.get("mode") or 0) < 255:
            conn.execute(text("ALTER TABLE payments MODIFY mode VARCHAR(255) NOT NULL"))
        conn.execute(
            text(
                """
                UPDATE hostel_applications
                SET application_status = COALESCE(NULLIF(application_status, ''), status, 'Draft'),
                    current_step = CASE WHEN current_step IS NULL OR current_step < 1 THEN 8 ELSE current_step END,
                    allocation_status = COALESCE(NULLIF(allocation_status, ''), 'allocated')
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE rooms
                SET occupied_beds = COALESCE(occupied_beds, 0),
                    available_beds = COALESCE(available_beds, GREATEST(COALESCE(beds, 3), 0))
                """
            )
        )


def ensure_database_indexes() -> None:
    indexes = {
        "students": {
            "ix_students_active_created": ("is_active", "created_at", "id"),
            "ix_students_name": ("name",),
        },
        "hostel_applications": {
            "ix_app_student_updated": ("student_id", "updated_at", "id"),
            "ix_app_status_updated": ("application_status", "updated_at"),
            "ix_app_room_allocation": ("room_id", "allocation_status", "application_status"),
            "ix_app_hostel_status": ("hostel_id", "application_status"),
        },
        "payments": {
            "ix_payments_student_created": ("student_id", "created_at"),
            "ix_payments_application_type_status": ("application_id", "payment_type", "status"),
            "ix_payments_status_created": ("status", "created_at"),
        },
        "payment_receipts": {
            "ix_receipts_student_generated": ("student_id", "generated_at"),
            "ix_receipts_payment_type": ("payment_id", "receipt_type"),
        },
        "rooms": {
            "ix_rooms_hostel_floor_number": ("hostel_id", "floor", "room_number"),
            "ix_rooms_status": ("status",),
        },
    }
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            existing = {
                (row[0], row[1])
                for row in conn.execute(
                    text(
                        """
                        SELECT TABLE_NAME, INDEX_NAME
                        FROM INFORMATION_SCHEMA.STATISTICS
                        WHERE TABLE_SCHEMA = DATABASE()
                        """
                    )
                )
            }
            for table, table_indexes in indexes.items():
                for name, columns in table_indexes.items():
                    if (table, name) not in existing:
                        column_sql = ", ".join(f"`{column}`" for column in columns)
                        conn.execute(text(f"CREATE INDEX {name} ON {table} ({column_sql})"))
            return
        for table, table_indexes in indexes.items():
            for name, columns in table_indexes.items():
                column_sql = ", ".join(columns)
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column_sql})"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def save_or_409(action):
    try:
        return action()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate or invalid related record.",
        ) from exc
    except DataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="One or more fields exceed the allowed size or format.",
        ) from exc


def parse_frontend_token(authorization: str | None = None, token: str | None = None) -> tuple[str, int] | None:
    raw = token or ""
    if not raw and authorization:
        raw = authorization.strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
    if raw.startswith("mmc-student-"):
        try:
            return "student", int(raw.removeprefix("mmc-student-"))
        except ValueError:
            return None
    if raw.startswith("mmc-admin-"):
        try:
            return "admin", int(raw.removeprefix("mmc-admin-"))
        except ValueError:
            return None
    return None


def authorized_receipt_student_id(
    db: Session,
    student_id: int | None,
    authorization: str | None = None,
    token: str | None = None,
) -> int | None:
    parsed = parse_frontend_token(authorization, token)
    if not parsed:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required.")
    role, user_id = parsed
    if role == "admin":
        admin = db.get(models.AdminUser, user_id)
        if admin and admin.is_active:
            return student_id
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin session expired.")
    student = crud.get_student(db, user_id)
    if not student or not student.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Student session expired.")
    if student_id and student_id != student.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students can only access their own receipts.")
    return student.id


def authorize_receipt_access(
    db: Session,
    receipt: models.PaymentReceipt,
    authorization: str | None = None,
    token: str | None = None,
) -> None:
    scoped_student_id = authorized_receipt_student_id(db, receipt.student_id, authorization, token)
    if scoped_student_id is not None and receipt.student_id != scoped_student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Receipt does not belong to this student.")


def settings_response(settings_model: models.AdmissionPaymentSettings) -> schemas.ApplicationSettingsRead:
    today = date.today()

    def state(start: date | None, end: date | None, kind: str) -> tuple[str, str | None]:
        if start and today < start:
            return "not_started", f"{kind} has not started yet."
        if end and today > end:
            return "closed", f"{kind} is Closed." if kind == "Hostel Admission" else "Payment deadline has expired."
        return "open", None

    admission_state, admission_message = state(
        settings_model.admission_start_date,
        settings_model.admission_end_date,
        "Hostel Admission",
    )
    payment_state, payment_message = state(
        settings_model.payment_start_date,
        settings_model.payment_end_date,
        "Payment",
    )
    return schemas.ApplicationSettingsRead(
        admission_start_date=settings_model.admission_start_date,
        admission_end_date=settings_model.admission_end_date,
        payment_start_date=settings_model.payment_start_date,
        payment_end_date=settings_model.payment_end_date,
        admission_state=admission_state,
        payment_state=payment_state,
        admission_message=admission_message,
        payment_message=payment_message,
    )


def require_admission_open(db: Session, existing_draft: bool = False) -> None:
    if existing_draft:
        return
    settings_model = crud.get_application_settings(db)
    settings_data = settings_response(settings_model)
    if settings_data.admission_state == "not_started":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hostel Admission has not started yet.")
    if settings_data.admission_state == "closed":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hostel Admission is Closed.")


def require_payment_open(db: Session) -> None:
    settings_model = crud.get_application_settings(db)
    settings_data = settings_response(settings_model)
    if settings_data.payment_state == "not_started":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment has not started yet.")
    if settings_data.payment_state == "closed":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment deadline has expired.")


def parse_date_value(value):
    if not value or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def normalize_application_data(data: dict) -> dict:
    normalized = dict(data or {})
    if "date_of_birth" in normalized:
        normalized["date_of_birth"] = parse_date_value(normalized["date_of_birth"])
    for key in ("marks_obtained", "total_marks", "percentage"):
        if normalized.get(key) in ("", None):
            normalized[key] = None
        elif key in normalized:
            normalized[key] = Decimal(str(normalized[key]))
    for key, value in list(normalized.items()):
        if value == "":
            normalized[key] = None
    return normalized


STEP_REQUIRED_FIELDS = {
    1: ["application_type"],
    2: ["name", "gender", "date_of_birth", "mobile", "email", "aadhar_number", "applied_category"],
    3: ["admission_level", "admission_id", "college_name", "course", "subject", "session", "roll_number"],
    4: ["intermediate_college", "board", "previous_course", "total_marks", "marks_obtained", "result_type", "percentage"],
    5: ["father_name", "mother_name"],
    6: ["permanent_address", "correspondence_address"],
    7: ["student_photo_data", "aadhar_card_data", "admission_receipt_data"],
    8: [],
}


def validate_step_payload(step: int, data: dict) -> None:
    missing = [field for field in STEP_REQUIRED_FIELDS.get(step, []) if data.get(field) in (None, "")]
    if step == 1 and str(data.get("application_type") or "").lower() == "existing":
        for field in ("existing_hostel_name", "existing_room_number", "existing_previous_session"):
            if data.get(field) in (None, ""):
                missing.append(field)
    if step == 7:
        category = str(data.get("applied_category") or data.get("category") or "").upper()
        if category == "EWS" and data.get("income_certificate_data") in (None, ""):
            missing.append("income_certificate_data")
        if category in {"BC", "EBC", "SC", "ST"} and data.get("caste_certificate_data") in (None, ""):
            missing.append("caste_certificate_data")
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required field(s) for step {step}: {', '.join(missing)}",
        )
    mobile = data.get("mobile")
    if mobile and (not str(mobile).isdigit() or len(str(mobile)) != 10):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Mobile number must be 10 digits.")
    guardian_mobile = data.get("guardian_mobile")
    if guardian_mobile and (not str(guardian_mobile).isdigit() or len(str(guardian_mobile)) != 10):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Guardian mobile must be 10 digits.")
    aadhar = data.get("aadhar_number")
    if aadhar and (not str(aadhar).isdigit() or len(str(aadhar)) != 12):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Aadhar number must be 12 digits.")
    total = data.get("total_marks")
    marks = data.get("marks_obtained")
    if total is not None and marks is not None and Decimal(str(marks)) > Decimal(str(total)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Marks obtained cannot exceed total marks.")


@app.post("/admins", response_model=schemas.AdminRead, status_code=status.HTTP_201_CREATED)
def create_admin(payload: schemas.AdminCreate, db: Session = Depends(get_db)):
    return save_or_409(lambda: crud.create_admin(db, payload))


@app.get("/admins", response_model=list[schemas.AdminRead])
def list_admins(db: Session = Depends(get_db)):
    return crud.list_admins(db)


@app.post("/auth/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    if payload.role == "auto":
        student = crud.authenticate_student(db, payload.identifier, payload.password)
        if student:
            return {"role": "student", "user": student}
        admin = crud.authenticate_admin(db, payload.identifier, payload.password)
        if admin:
            return {"role": "admin", "user": admin}
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login credentials.")
    if payload.role == "admin":
        admin = crud.authenticate_admin(db, payload.identifier, payload.password)
        if not admin:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials.")
        return {"role": "admin", "user": admin}
    if payload.role == "student":
        student = crud.authenticate_student(db, payload.identifier, payload.password)
        if not student:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid student credentials.")
        return {"role": "student", "user": student}
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported login role.")


@app.post("/students", response_model=schemas.StudentRead, status_code=status.HTTP_201_CREATED)
def create_student(payload: schemas.StudentCreate, db: Session = Depends(get_db)):
    return save_or_409(lambda: crud.create_student(db, payload))


@app.post("/students/register", response_model=schemas.StudentRead, status_code=status.HTTP_201_CREATED)
def register_student(payload: schemas.StudentRegister, db: Session = Depends(get_db)):
    return save_or_409(lambda: crud.register_student(db, payload))


@app.get("/students", response_model=list[schemas.StudentRead])
def list_students(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud.list_students(db, skip=skip, limit=limit)


@app.get("/students/{student_id}", response_model=schemas.StudentRead)
def get_student(student_id: int, db: Session = Depends(get_db)):
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return student


@app.patch("/students/{student_id}", response_model=schemas.StudentRead)
def update_student(student_id: int, payload: schemas.StudentUpdate, db: Session = Depends(get_db)):
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return save_or_409(lambda: crud.update_student(db, student, payload))


@app.patch("/students/{student_id}/password", response_model=schemas.StudentRead)
def update_student_password(
    student_id: int,
    payload: schemas.StudentPasswordUpdate,
    db: Session = Depends(get_db),
):
    student = crud.get_student(db, student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return save_or_409(lambda: crud.update_student_password(db, student, payload.password))


@app.post("/hostels", response_model=schemas.HostelRead, status_code=status.HTTP_201_CREATED)
def create_hostel(payload: schemas.HostelCreate, db: Session = Depends(get_db)):
    return save_or_409(lambda: crud.create_hostel(db, payload))


@app.get("/hostels", response_model=list[schemas.HostelRead])
def list_hostels(db: Session = Depends(get_db)):
    return crud.list_hostels(db)


@app.patch("/hostels/{hostel_id}", response_model=schemas.HostelRead)
def update_hostel(hostel_id: int, payload: schemas.HostelUpdate, db: Session = Depends(get_db)):
    hostel = crud.get_hostel(db, hostel_id)
    if not hostel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hostel not found.")
    return save_or_409(lambda: crud.update_hostel(db, hostel, payload))


@app.post("/rooms", response_model=schemas.RoomRead, status_code=status.HTTP_201_CREATED)
def create_room(payload: schemas.RoomCreate, db: Session = Depends(get_db)):
    return save_or_409(lambda: crud.create_room(db, payload))


@app.get("/rooms", response_model=list[schemas.RoomRead])
def list_rooms(hostel_id: int | None = None, db: Session = Depends(get_db)):
    return crud.list_rooms(db, hostel_id=hostel_id, sync=True)


@app.get("/rooms/{room_id}/beds")
def get_room_bed_inventory(room_id: int, db: Session = Depends(get_db)):
    room = crud.get_room(db, room_id)
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    occupied_applications = list(
        db.scalars(
            select(models.HostelApplication)
            .options(joinedload(models.HostelApplication.student))
            .where(
                models.HostelApplication.room_id == room.id,
                models.HostelApplication.allocation_status != "vacated",
                models.HostelApplication.bed.is_not(None),
                models.HostelApplication.application_status != "Draft",
            )
        )
    )
    applications_by_bed = {app.bed: app for app in occupied_applications if app.bed}
    bed_labels = ["A", "B", "C"][: max(int(room.beds or 0), 0)]
    available_beds = [bed for bed in bed_labels if bed not in applications_by_bed]
    occupied_beds = [bed for bed in bed_labels if bed in applications_by_bed]
    bed_details = []
    for bed in bed_labels:
        application = applications_by_bed.get(bed)
        student = application.student if application else None
        bed_details.append(
            {
                "bed": bed,
                "bed_number": f"{room.room_number} - {bed}",
                "status": "occupied" if application else "available",
                "student_name": student.name if student else None,
                "student_db_id": student.id if student else None,
                "student_id": student.student_code if student else None,
                "registration_number": student.student_code if student else None,
                "application_id": application.id if application else None,
                "application_no": application.application_no if application else None,
                "allocation_date": application.allocation_date.isoformat()
                if application and application.allocation_date
                else None,
            }
        )
    return {
        "room_id": room.id,
        "room_number": room.room_number,
        "total_beds": max(int(room.beds or 0), 0),
        "occupied_beds": len(occupied_beds),
        "available_beds": len(available_beds),
        "remaining_beds": len(available_beds),
        "occupied_bed_numbers": occupied_beds,
        "available_bed_numbers": available_beds,
        "beds": bed_details,
        "status": "full" if not available_beds else ("available" if len(available_beds) > 0 else "full"),
    }


@app.post("/applications/{application_id}/allocate-bed")
def assign_bed_to_application(application_id: int, payload: schemas.AllocationRequest, db: Session = Depends(get_db)):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    crud.assign_application_bed(
        db,
        application,
        room_id=payload.room_id,
        bed=payload.bed,
        hostel_id=payload.hostel_id,
        block=payload.block,
        floor=payload.floor,
        allocation_date=payload.allocation_date,
        allocation_status=payload.allocation_status,
    )
    db.commit()
    db.refresh(application)
    return {"message": "Bed assigned successfully.", "application": application}


@app.post("/applications/{application_id}/transfer")
def transfer_application_bed(application_id: int, payload: schemas.AllocationRequest, db: Session = Depends(get_db)):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    crud.assign_application_bed(
        db,
        application,
        room_id=payload.room_id,
        bed=payload.bed,
        hostel_id=payload.hostel_id,
        block=payload.block,
        floor=payload.floor,
        allocation_date=payload.allocation_date,
        allocation_status=payload.allocation_status,
    )
    db.commit()
    db.refresh(application)
    return {"message": "Student transferred successfully.", "application": application}


@app.post("/applications/{application_id}/checkout")
def checkout_application_bed(application_id: int, db: Session = Depends(get_db)):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    crud.release_application_bed(db, application)
    db.commit()
    db.refresh(application)
    return {"message": "Bed released successfully.", "application": application}


@app.patch("/rooms/{room_id}", response_model=schemas.RoomRead)
def update_room(room_id: int, payload: schemas.RoomUpdate, db: Session = Depends(get_db)):
    room = crud.get_room(db, room_id)
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    return save_or_409(lambda: crud.update_room(db, room, payload))


@app.post("/applications", response_model=schemas.ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: schemas.ApplicationCreate,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    scoped_student_id = authorized_receipt_student_id(db, payload.student_id, authorization, token)
    if scoped_student_id and scoped_student_id != payload.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students can only create their own applications.")
    if not crud.get_student(db, payload.student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    require_admission_open(db)
    data = normalize_application_data(payload.model_dump())
    for step in range(1, 8):
        validate_step_payload(step, data)
    if crud.get_student_application_for_session(db, payload.student_id, payload.session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A hostel application already exists for this session.",
        )
    data = upload_application_documents(data, student_id=payload.student_id)
    return save_or_409(lambda: crud.create_application(db, schemas.ApplicationCreate.model_validate(data)))


@app.post("/applications/validate-step")
def validate_application_step(payload: schemas.ApplicationDraftValidate) -> dict[str, str]:
    data = normalize_application_data(payload.data)
    validate_step_payload(payload.step, data)
    return {"status": "ok"}


@app.post("/applications/draft", response_model=schemas.ApplicationRead)
def save_application_draft(
    payload: schemas.ApplicationDraftSave,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    scoped_student_id = authorized_receipt_student_id(db, payload.student_id, authorization, token)
    if scoped_student_id and scoped_student_id != payload.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students can only update their own applications.")
    student = crud.get_student(db, payload.student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    existing_latest = crud.get_latest_student_application(db, student.id)
    latest_status = str((existing_latest.application_status or existing_latest.status) if existing_latest else "").lower()
    if existing_latest and latest_status != "draft":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Application has already been submitted. Student-side editing is disabled. Contact admin for changes.",
        )
    existing_draft = existing_latest
    require_admission_open(db, existing_draft=bool(existing_draft))
    data = normalize_application_data(payload.data)
    validate_step_payload(payload.current_step, data)
    session_value = data.get("session")
    if session_value:
        duplicate = crud.get_student_application_for_session(db, student.id, session_value)
        if duplicate and (not existing_draft or duplicate.id != existing_draft.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A hostel application already exists for this session.",
            )
    previous_documents = {
        field: getattr(existing_draft, field, None)
        for field in ("student_photo_data", "aadhar_card_data", "admission_receipt_data", "income_certificate_data", "caste_certificate_data")
    } if existing_draft else {}
    data = upload_application_documents(
        data,
        student_id=student.id,
        application_id=existing_draft.id if existing_draft else None,
        previous_values=previous_documents,
    )
    return save_or_409(lambda: crud.save_application_draft(db, student, payload.current_step, data))


@app.get("/applications/resume/{student_id}", response_model=schemas.ApplicationRead | None)
def resume_application(
    student_id: int,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    scoped_student_id = authorized_receipt_student_id(db, student_id, authorization, token)
    if scoped_student_id and scoped_student_id != student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students can only resume their own applications.")
    if not crud.get_student(db, student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return crud.get_latest_student_application(db, student_id)


@app.post("/applications/{application_id}/submit", response_model=schemas.ApplicationRead)
def submit_application(
    application_id: int,
    payload: schemas.ApplicationDraftSave,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    scoped_student_id = authorized_receipt_student_id(db, payload.student_id, authorization, token)
    if scoped_student_id and scoped_student_id != payload.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students can only submit their own applications.")
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    if application.student_id != payload.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Application does not belong to this student.")
    application_status = str(application.application_status or application.status or "").lower()
    if application_status != "draft":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Application has already been submitted. Student-side editing is disabled. Contact admin for changes.",
        )
    require_admission_open(db, existing_draft=True)
    data = normalize_application_data(payload.data)
    merged = {field: getattr(application, field, None) for field in crud.APPLICATION_DRAFT_FIELDS}
    merged.update(data)
    merged.update({
        "name": application.student.name,
        "email": application.student.email,
        "mobile": application.student.mobile,
        "date_of_birth": application.student.date_of_birth,
        "gender": application.student.gender,
    })
    for step in range(1, 8):
        validate_step_payload(step, merged)
    previous_documents = {
        field: getattr(application, field, None)
        for field in ("student_photo_data", "aadhar_card_data", "admission_receipt_data", "income_certificate_data", "caste_certificate_data")
    }
    data = upload_application_documents(data, student_id=application.student_id, application_id=application.id, previous_values=previous_documents)
    return save_or_409(lambda: crud.submit_application(db, application, data))


@app.get("/applications", response_model=list[schemas.ApplicationRead])
def list_applications(
    status_filter: str | None = None,
    student_id: int | None = None,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    if student_id is not None:
        scoped_student_id = authorized_receipt_student_id(db, student_id, authorization, token)
        return crud.list_applications(db, status=status_filter, student_id=scoped_student_id)
    return crud.list_applications(db, status=status_filter, student_id=student_id)


@app.get("/applications/{application_id}", response_model=schemas.ApplicationRead)
def get_application(
    application_id: int,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    scoped_student_id = authorized_receipt_student_id(db, application.student_id, authorization, token)
    if scoped_student_id and scoped_student_id != application.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Students can only view their own applications.")
    return application


@app.patch("/applications/{application_id}/status", response_model=schemas.ApplicationRead)
def update_application_status(
    application_id: int,
    payload: schemas.ApplicationStatusUpdate,
    db: Session = Depends(get_db),
):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    return save_or_409(lambda: crud.update_application_status(db, application, payload))


@app.get("/settings/application", response_model=schemas.ApplicationSettingsRead)
@app.get("/api/settings/application", response_model=schemas.ApplicationSettingsRead)
def get_application_settings(db: Session = Depends(get_db)):
    return settings_response(crud.get_application_settings(db))


@app.put("/settings/application", response_model=schemas.ApplicationSettingsRead)
@app.put("/api/settings/application", response_model=schemas.ApplicationSettingsRead)
def update_application_settings(payload: schemas.ApplicationSettingsUpdate, db: Session = Depends(get_db)):
    if payload.admission_start_date and payload.admission_end_date and payload.admission_start_date > payload.admission_end_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Admission start date cannot be after last date.")
    if payload.payment_start_date and payload.payment_end_date and payload.payment_start_date > payload.payment_end_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Payment start date cannot be after last date.")
    return settings_response(crud.update_application_settings(db, payload))


@app.get("/admin/dashboard-metrics", response_model=schemas.AdminDashboardMetrics)
def admin_dashboard_metrics(db: Session = Depends(get_db)):
    settings_model = crud.get_application_settings(db)
    settings_data = settings_response(settings_model)
    counts = crud.count_applications_by_status(db)
    today = date.today()

    def countdown(end_date: date | None) -> str | None:
        if not end_date:
            return None
        days = (end_date - today).days
        if days < 0:
            return "Closed"
        if days == 0:
            return "Closes today"
        return f"{days} day{'s' if days != 1 else ''} left"

    rooms = crud.list_rooms(db)
    room_status_summary = {status: 0 for status in ["available", "occupied", "reserved", "maintenance"]}
    for room in rooms:
        room_status_summary[room.status] = room_status_summary.get(room.status, 0) + 1
    total_rooms = len(rooms)
    occupied_rooms = sum(1 for room in rooms if room.status == "occupied")
    available_rooms = sum(1 for room in rooms if room.status == "available")
    total_beds = sum(max(int(room.beds or 0), 0) for room in rooms)
    occupied_beds = sum(max(int(room.occupied_beds or 0), 0) for room in rooms)
    available_beds = max(total_beds - occupied_beds, 0)
    hostel_occupancy_pct = round((occupied_beds / total_beds * 100) if total_beds else 0.0, 1)
    recent_allocations = []
    for application in sorted(
        [app for app in crud.list_applications(db) if app.room_id and app.bed and app.allocation_status != "vacated"],
        key=lambda item: item.updated_at or item.created_at,
        reverse=True,
    )[:8]:
        recent_allocations.append(
            {
                "application_no": application.application_no,
                "student_name": application.student.name if application.student else None,
                "room_number": application.room.room_number if application.room else None,
                "bed": application.bed,
                "allocation_date": application.allocation_date.isoformat() if application.allocation_date else None,
            }
        )
    recent_vacated_beds = []
    for application in sorted(
        [app for app in crud.list_applications(db) if app.allocation_status == "vacated"],
        key=lambda item: item.updated_at or item.created_at,
        reverse=True,
    )[:8]:
        recent_vacated_beds.append(
            {
                "application_no": application.application_no,
                "student_name": application.student.name if application.student else None,
                "room_number": application.room.room_number if application.room else None,
                "bed": application.bed,
            }
        )
    return schemas.AdminDashboardMetrics(
        settings=settings_data,
        countdown_to_admission_closing=countdown(settings_model.admission_end_date),
        countdown_to_payment_closing=countdown(settings_model.payment_end_date),
        total_draft_applications=counts.get("Draft", 0),
        total_submitted_applications=counts.get("Submitted", 0),
        total_approved_applications=counts.get("Approved", 0) + counts.get("Selected", 0),
        total_rejected_applications=counts.get("Rejected", 0),
        total_rooms=total_rooms,
        occupied_rooms=occupied_rooms,
        available_rooms=available_rooms,
        total_beds=total_beds,
        occupied_beds=occupied_beds,
        available_beds=available_beds,
        hostel_occupancy_pct=hostel_occupancy_pct,
        recent_allocations=recent_allocations,
        recent_vacated_beds=recent_vacated_beds,
        room_status_summary=room_status_summary,
    )


@app.post("/payments", response_model=schemas.PaymentRead, status_code=status.HTTP_201_CREATED)
def create_payment(payload: schemas.PaymentCreate, db: Session = Depends(get_db)):
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Direct payment creation is disabled. Use /api/payment/initiate.",
    )


def require_payment_application(student_id: int, application_id: int, db: Session):
    application = crud.get_application(db, application_id)
    if not application or application.student_id != student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student application not found.")
    return application


def expected_registration_fee(application: models.HostelApplication) -> Decimal:
    return Decimal("100") if application.application_type == "existing" else Decimal("1000")


def expected_hostel_fee(application: models.HostelApplication) -> Decimal:
    application_state = (application.application_status or application.status or "").lower()
    if application_state not in {"selected", "shortlisted", "approved", "published", "room allocated", "room_allocated"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hostel fee requires a shortlisted, published, or approved application.",
        )
    if not application.hostel:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A hostel must be allotted first.")
    hostel_name = application.hostel.name.strip().lower()
    if application.hostel.fee and application.hostel.fee > 0:
        return application.hostel.fee
    if "mahima" in hostel_name:
        return Decimal("12000")
    if "vaidehi" in hostel_name:
        return Decimal("10000")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported hostel fee configuration.")


def normalize_payment_type(payment_type: str) -> str:
    value = (payment_type or "").strip().lower()
    if "hostel" in value:
        return "Hostel Admission Fee"
    if "registration" in value:
        return "Registration Fee"
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported payment type.")


def validate_payment_initiation(payload: schemas.PaymentInitiateRequest, db: Session) -> models.HostelApplication:
    require_payment_open(db)
    if not crud.get_student(db, payload.student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    application = require_payment_application(payload.student_id, payload.application_id, db)
    if (application.application_status or application.status or "").lower() == "draft":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Submit your application before payment.")
    payment_type = normalize_payment_type(payload.payment_type)
    if crud.get_successful_payment_for_application(db, application.id, payment_type):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Hostel fee has already been paid for this application."
                if payment_type == "Hostel Admission Fee"
                else "Registration fee has already been paid for this application."
            ),
        )
    expected = expected_hostel_fee(application) if payment_type == "Hostel Admission Fee" else expected_registration_fee(application)
    if payload.amount != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{payment_type} must be Rs. {expected:.2f}.",
        )
    return application


def ccavenue_redirect_url(path: str) -> str:
    return f"{settings.base_url}{path}"


def ccavenue_cipher() -> AES:
    if not settings.ccavenue_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CCAvenue payment gateway is not configured.",
        )
    key = hashlib.md5(settings.ccavenue_working_key.encode("utf-8")).digest()
    iv = bytes(range(16))
    return AES.new(key, AES.MODE_CBC, iv)


def pkcs7_pad(value: bytes) -> bytes:
    padding = AES.block_size - (len(value) % AES.block_size)
    return value + bytes([padding]) * padding


def pkcs7_unpad(value: bytes) -> bytes:
    if not value:
        raise ValueError("Empty encrypted response.")
    padding = value[-1]
    if padding < 1 or padding > AES.block_size:
        raise ValueError("Invalid encrypted response padding.")
    return value[:-padding]


def ccavenue_encrypt(plain_text: str) -> str:
    cipher = ccavenue_cipher()
    return cipher.encrypt(pkcs7_pad(plain_text.encode("utf-8"))).hex()


def ccavenue_decrypt(encrypted_text: str) -> str:
    try:
        encrypted_bytes = bytes.fromhex(encrypted_text)
        cipher = ccavenue_cipher()
        return pkcs7_unpad(cipher.decrypt(encrypted_bytes)).decode("utf-8")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payment gateway response.",
        ) from exc


def ccavenue_sub_account_id(payment_type: str) -> str:
    return HOSTEL_CCAVENUE_SUB_ACCOUNT_ID


def build_ccavenue_request(payment: models.Payment, application: models.HostelApplication) -> str:
    student = payment.student
    data = {
        "merchant_id": settings.ccavenue_merchant_id,
        "order_id": payment.transaction_no,
        "currency": settings.ccavenue_currency,
        "amount": f"{payment.amount:.2f}",
        "redirect_url": ccavenue_redirect_url("/api/payment/ccavenue/response"),
        "cancel_url": ccavenue_redirect_url("/api/payment/ccavenue/cancel"),
        "language": "EN",
        "billing_name": student.name if student else "",
        "billing_email": student.email if student else "",
        "billing_tel": student.mobile if student else "",
        "merchant_param1": str(payment.student_id),
        "merchant_param2": str(payment.application_id or ""),
        "merchant_param3": payment.payment_type,
        "merchant_param4": application.application_no,
    }
    sub_account_id = ccavenue_sub_account_id(payment.payment_type)
    if sub_account_id:
        data["sub_account_id"] = sub_account_id
    return urlencode(data)


def payment_return_page(title: str, message: str, receipt_url: str | None = None) -> HTMLResponse:
    receipt_link = f'<p><a href="{receipt_url}" target="_blank" rel="noopener">Download Receipt</a></p>' if receipt_url else ""
    return_url = settings.hostel_erp_frontend_return_url
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>
<body style="font-family:Arial,sans-serif;max-width:720px;margin:48px auto;padding:0 20px;line-height:1.5;">
<h1>{title}</h1>
<p>{message}</p>
{receipt_link}
<p><a href="{return_url}">Return to ERP</a></p>
</body>
</html>"""
    )


def generate_payment_receipt_safely(db: Session, payment: models.Payment, receipt_type: str) -> models.PaymentReceipt | None:
    try:
        return receipt_service.generate_receipt_pdf(db, payment, receipt_type)
    except Exception:
        db.rollback()
        logger.exception("Receipt generation failed for paid payment order=%s.", payment.transaction_no)
        return None


@app.post("/api/payment/initiate", response_model=schemas.PaymentInitiateResponse)
def initiate_payment(payload: schemas.PaymentInitiateRequest, db: Session = Depends(get_db)):
    application = validate_payment_initiation(payload, db)
    payment_type = normalize_payment_type(payload.payment_type)
    lock_key = (application.id, payment_type)
    with payment_initiation_locks_guard:
        initiation_lock = payment_initiation_locks.setdefault(lock_key, Lock())
    with initiation_lock:
        payment = crud.get_pending_payment_for_application(db, application.id, payment_type)
        if payment and payment.created_at and payment.created_at < datetime.now() - timedelta(minutes=30):
            payment.status = "Cancelled"
            payment.failure_reason = "Expired payment session replaced by a new payment request."
            db.commit()
            payment = None
        if not payment:
            order_id = f"MMC{datetime.now().strftime('%Y%m%d%H%M%S%f')}{application.id}"[:50]
            payment_payload = schemas.PaymentCreate(
                student_id=payload.student_id,
                application_id=application.id,
                payment_type=payment_type,
                amount=payload.amount,
                currency=settings.ccavenue_currency.upper(),
                mode="CCAvenue",
                status="Pending",
                sub_account_id=ccavenue_sub_account_id(payment_type),
                transaction_no=order_id,
                paid_at=None,
            )
            payment = save_or_409(lambda: crud.create_payment(db, payment_payload))
    logger.info(
        "CCAvenue payment request initiated order=%s student_id=%s application_id=%s type=%s amount=%s sub_account_id=%s",
        payment.transaction_no,
        payment.student_id,
        payment.application_id,
        payment.payment_type,
        payment.amount,
        HOSTEL_CCAVENUE_SUB_ACCOUNT_ID,
    )
    enc_request = ccavenue_encrypt(build_ccavenue_request(payment, application))
    return schemas.PaymentInitiateResponse(
        gateway_url=settings.ccavenue_gateway_url,
        encRequest=enc_request,
        access_code=settings.ccavenue_access_code,
    )


@app.get("/api/payment/status/{order_id}")
def payment_status(order_id: str, db: Session = Depends(get_db)):
    payment = crud.get_payment_by_transaction_no(db, order_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found.")
    if (payment.status or "").lower() in {"paid", "success", "completed"}:
        receipt_service.ensure_receipts_for_successful_payments(db, student_id=payment.student_id, max_generate=1)
        db.refresh(payment)
    receipt = payment.receipts[0] if payment.receipts else None
    status_key = (payment.status or "").lower()
    return {
        "order_id": payment.transaction_no,
        "tracking_id": payment.tracking_id or payment.transaction_no,
        "bank_ref_no": payment.bank_ref_no,
        "payment_mode": payment.mode,
        "payment_status": payment.status,
        "status": "success" if status_key in {"paid", "success", "completed"} else ("cancelled" if status_key in {"cancelled", "canceled", "aborted"} else status_key or "pending"),
        "failure_reason": payment.failure_reason,
        "amount": float(payment.amount or 0),
        "currency": payment.currency or settings.ccavenue_currency.upper(),
        "sub_account_id": payment.sub_account_id or HOSTEL_CCAVENUE_SUB_ACCOUNT_ID,
        "payment_date": payment.paid_at,
        "receipt_url": f"/receipts/{receipt.id}/download" if receipt else None,
    }


def handle_ccavenue_response(enc_resp: str, db: Session) -> HTMLResponse:
    response_text = ccavenue_decrypt(enc_resp)
    values = {key: items[0] for key, items in parse_qs(response_text, keep_blank_values=True).items()}
    order_id = values.get("order_id") or ""
    payment = crud.get_payment_by_transaction_no(db, order_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found.")
    logger.info("CCAvenue payment response received order=%s status=%s", order_id, values.get("order_status"))
    existing_status = (payment.status or "").lower()
    if existing_status in {"paid", "success", "completed"}:
        receipt_type = "hostel_admission" if "hostel" in payment.payment_type.lower() else "application_registration"
        receipt = generate_payment_receipt_safely(db, payment, receipt_type)
        receipt_url = (receipt.pdf_url or f"/receipts/{receipt.id}/download") if receipt else None
        return payment_return_page(
            "Payment Already Verified",
            "This payment was already verified. Your receipt will be available in the ERP receipts page.",
            receipt_url,
        )
    order_status = (values.get("order_status") or "").strip()
    tracking_id = values.get("tracking_id") or ""
    bank_ref_no = values.get("bank_ref_no") or ""
    gateway_tracking_id = tracking_id or bank_ref_no or order_id
    gateway_mode = values.get("payment_mode") or "CCAvenue"
    card_name = values.get("card_name")
    if gateway_tracking_id and gateway_tracking_id != order_id:
        gateway_mode = f"{gateway_mode} ({gateway_tracking_id})"
    if card_name:
        gateway_mode = f"{gateway_mode} - {card_name}"
    paid_at = datetime.now()
    if values.get("trans_date"):
        try:
            paid_at = datetime.strptime(values["trans_date"], "%d/%m/%Y %H:%M:%S")
        except ValueError:
            paid_at = datetime.now()
    gateway_response = json.dumps(values, ensure_ascii=True, sort_keys=True)
    failure_reason = (
        values.get("failure_message")
        or values.get("status_message")
        or values.get("status_message2")
        or values.get("vault")
        or order_status
        or "Payment failed"
    )
    if order_status.lower() == "success":
        try:
            response_amount = Decimal(values.get("amount") or "0")
        except InvalidOperation:
            response_amount = Decimal("0")
        response_currency = (values.get("currency") or settings.ccavenue_currency).upper()
        if response_amount != payment.amount or response_currency != settings.ccavenue_currency.upper():
            crud.update_payment_gateway_result(
                db,
                payment,
                mode=gateway_mode,
                status="Invalid",
                tracking_id=tracking_id,
                bank_ref_no=bank_ref_no,
                failure_reason="CCAvenue returned mismatched amount or currency.",
                currency=response_currency,
                sub_account_id=HOSTEL_CCAVENUE_SUB_ACCOUNT_ID,
                gateway_response=gateway_response,
            )
            return payment_return_page("Payment Verification Failed", "CCAvenue returned a payment amount or currency that does not match this order.")
        payment = crud.update_payment_gateway_result(
            db,
            payment,
            mode=gateway_mode,
            status="Paid",
            tracking_id=tracking_id,
            bank_ref_no=bank_ref_no,
            failure_reason="",
            currency=response_currency,
            sub_account_id=HOSTEL_CCAVENUE_SUB_ACCOUNT_ID,
            gateway_response=gateway_response,
            paid_at=paid_at,
        )
        receipt_type = "hostel_admission" if "hostel" in payment.payment_type.lower() else "application_registration"
        receipt = generate_payment_receipt_safely(db, payment, receipt_type)
        receipt_url = (receipt.pdf_url or f"/receipts/{receipt.id}/download") if receipt else None
        return payment_return_page(
            "Payment Successful",
            "Your payment has been verified. Your receipt is available now or will appear shortly in the ERP receipts page.",
            receipt_url,
        )
    failed_status = "Cancelled" if order_status.lower() in {"aborted", "cancelled", "canceled", "cancel"} else (order_status or "Failed")
    crud.update_payment_gateway_result(
        db,
        payment,
        mode=gateway_mode,
        status=failed_status,
        tracking_id=tracking_id,
        bank_ref_no=bank_ref_no,
        failure_reason=failure_reason,
        currency=(values.get("currency") or settings.ccavenue_currency).upper(),
        sub_account_id=HOSTEL_CCAVENUE_SUB_ACCOUNT_ID,
        gateway_response=gateway_response,
    )
    return payment_return_page("Payment Not Completed", "CCAvenue did not confirm this payment. Please try again from the ERP portal.")


@app.post("/api/payment/ccavenue/response", response_class=HTMLResponse)
def ccavenue_payment_response(encResp: str = Form(...), db: Session = Depends(get_db)):
    return handle_ccavenue_response(encResp, db)


@app.post("/api/payment/ccavenue/cancel", response_class=HTMLResponse)
def ccavenue_payment_cancel(encResp: str = Form(...), db: Session = Depends(get_db)):
    return handle_ccavenue_response(encResp, db)


@app.post("/api/payment/ccavenue/notification", response_class=HTMLResponse)
def ccavenue_payment_notification(encResp: str = Form(...), db: Session = Depends(get_db)):
    return handle_ccavenue_response(encResp, db)


@app.get("/payments", response_model=list[schemas.PaymentRead])
def list_payments(
    student_id: int | None = None,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    scoped_student_id = authorized_receipt_student_id(db, student_id, authorization, token)
    receipt_service.ensure_receipts_for_successful_payments(db, student_id=scoped_student_id, max_generate=1)
    return crud.list_payments(db, student_id=scoped_student_id)


@app.get("/receipts", response_model=list[schemas.PaymentReceiptRead])
def list_receipts(
    student_id: int | None = None,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    scoped_student_id = authorized_receipt_student_id(db, student_id, authorization, token)
    receipt_service.ensure_receipts_for_successful_payments(db, student_id=scoped_student_id)
    return crud.list_receipts(db, student_id=scoped_student_id)


@app.post("/receipts/generate", response_model=schemas.PaymentReceiptRead, status_code=status.HTTP_201_CREATED)
def generate_receipt(payload: schemas.ReceiptGenerateRequest, db: Session = Depends(get_db)):
    payment = crud.get_payment(db, payload.payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found.")
    if payment.status.lower() not in {"paid", "success", "completed"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment is not successful.")
    receipt_type = payload.receipt_type or receipt_service.infer_receipt_type(payment)
    if receipt_type == "hostel_admission":
        application = payment.application
        application_state = (application.application_status or application.status or "").lower() if application else ""
        if not application or application_state not in {"selected", "shortlisted", "approved", "published", "room allocated", "room_allocated"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Application is not shortlisted.")
    return receipt_service.generate_receipt_pdf(db, payment, receipt_type)


@app.post("/receipts/{receipt_id}/regenerate", response_model=schemas.PaymentReceiptRead)
def regenerate_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = crud.get_receipt(db, receipt_id)
    if not receipt or not receipt.payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt payment not found.")
    return receipt_service.generate_receipt_pdf(db, receipt.payment, receipt.receipt_type)


@app.get("/receipts/verify/{receipt_number}")
def verify_receipt(receipt_number: str, db: Session = Depends(get_db)):
    receipt = crud.get_receipt_by_number(db, receipt_number)
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")
    return {
        "valid": True,
        "receipt_number": receipt.receipt_number,
        "receipt_type": receipt.receipt_type,
        "application_number": receipt.application_number,
        "student_id": receipt.student_id,
        "amount": receipt.amount,
        "transaction_id": receipt.transaction_id,
        "hostel_name": receipt.hostel_name,
        "room_number": receipt.room_number,
        "generated_at": receipt.generated_at,
    }


@app.get("/receipts/{receipt_id}", response_model=schemas.PaymentReceiptRead)
def get_receipt(
    receipt_id: int,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    receipt = crud.get_receipt(db, receipt_id)
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")
    authorize_receipt_access(db, receipt, authorization, token)
    return receipt


@app.get("/receipts/{receipt_id}/download")
def download_receipt(
    receipt_id: int,
    authorization: str | None = Header(None),
    token: str | None = None,
    db: Session = Depends(get_db),
):
    receipt = crud.get_receipt(db, receipt_id)
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")
    authorize_receipt_access(db, receipt, authorization, token)

    # Prefer the already-generated PDF. Regenerating on every download is slow
    # and can fail if external image assets are temporarily unreachable.
    pdf_bytes = receipt_service.get_receipt_pdf_bytes(receipt.receipt_number)
    if pdf_bytes:
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{receipt.receipt_number}.pdf"'},
        )

    if receipt.pdf_url and receipt.pdf_url.startswith(("http://", "https://")):
        return RedirectResponse(receipt.pdf_url)

    if not receipt.payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt PDF not found.")

    try:
        updated_receipt = receipt_service.generate_receipt_pdf(db, receipt.payment, receipt.receipt_type)
        receipt = updated_receipt
        pdf_bytes = receipt_service.get_receipt_pdf_bytes(receipt.receipt_number)
        if not pdf_bytes:
            pdf_bytes = receipt_service.build_receipt_pdf_bytes(receipt, receipt.payment, receipt.receipt_type)
    except Exception as exc:
        logger.exception("Could not generate receipt %s.", receipt.receipt_number)
        if receipt.pdf_url and receipt.pdf_url.startswith(("http://", "https://")):
            return RedirectResponse(receipt.pdf_url)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Receipt PDF could not be generated.") from exc
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{receipt.receipt_number}.pdf"'},
    )


@app.post("/upload/photo", response_model=schemas.FileUploadResponse)
async def upload_photo(file: UploadFile, student_id: int):
    """Upload a student photo to Cloudflare R2."""
    r2 = get_r2_service()
    if not r2.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is not configured.",
        )
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only image files are allowed.",
        )
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:  # 5 MB limit
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 5 MB limit.",
        )
    key = r2.photo_key(student_id, file.filename or "photo.jpg")
    url = r2.upload_bytes(content, key, content_type=file.content_type)
    return schemas.FileUploadResponse(
        url=url, key=key, content_type=file.content_type, size=len(content)
    )


@app.post("/upload/document", response_model=schemas.FileUploadResponse)
async def upload_document(file: UploadFile, category: str = "general"):
    """Upload a document (PDF, image, etc.) to Cloudflare R2."""
    r2 = get_r2_service()
    if not r2.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is not configured.",
        )
    allowed_types = {
        "application/pdf", "image/jpeg", "image/png", "image/webp",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{file.content_type}' is not allowed.",
        )
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10 MB limit
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 10 MB limit.",
        )
    key = r2.document_key(category, file.filename or "document")
    url = r2.upload_bytes(content, key, content_type=file.content_type or "application/octet-stream")
    return schemas.FileUploadResponse(
        url=url, key=key, content_type=file.content_type or "application/octet-stream", size=len(content)
    )


@app.get("/storage/status")
def storage_status():
    """Check if R2 cloud storage is configured and available."""
    r2 = get_r2_service()
    return {
        "r2_enabled": r2.enabled,
        "r2_public_url": settings.r2_public_base_url or None,
        "local_fallback": True,
    }
