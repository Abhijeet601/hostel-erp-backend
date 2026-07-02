from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from fastapi import Depends, FastAPI, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app import crud, models, receipt_service, schemas
from app.config import get_settings
from app.database import Base, engine, get_db
from app.r2_storage import get_r2_service


settings = get_settings()

app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_schema_updates()


def ensure_schema_updates() -> None:
    if engine.dialect.name != "mysql":
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
    }
    required_room_columns = {
        "occupied_beds": "INT NOT NULL DEFAULT 0",
        "available_beds": "INT NOT NULL DEFAULT 3",
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
        for column, ddl in required_application_columns.items():
            if column not in application_columns:
                conn.execute(text(f"ALTER TABLE hostel_applications ADD COLUMN {column} {ddl}"))
        for column, ddl in required_room_columns.items():
            if column not in room_columns:
                conn.execute(text(f"ALTER TABLE rooms ADD COLUMN {column} {ddl}"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY admission_id VARCHAR(50) NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY applied_category VARCHAR(20) NULL"))
        conn.execute(text("ALTER TABLE hostel_applications MODIFY student_photo_data LONGTEXT NULL"))
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
    7: ["student_photo_data"],
    8: [],
}


def validate_step_payload(step: int, data: dict) -> None:
    missing = [field for field in STEP_REQUIRED_FIELDS.get(step, []) if data.get(field) in (None, "")]
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
    return crud.list_rooms(db, hostel_id=hostel_id)


@app.get("/rooms/{room_id}/beds")
def get_room_bed_inventory(room_id: int, db: Session = Depends(get_db)):
    room = crud.get_room(db, room_id)
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found.")
    crud.sync_room_occupancy(db, room)
    occupied_applications = list(
        db.scalars(
            select(models.HostelApplication).where(
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
def create_application(payload: schemas.ApplicationCreate, db: Session = Depends(get_db)):
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
    return save_or_409(lambda: crud.create_application(db, payload))


@app.post("/applications/validate-step")
def validate_application_step(payload: schemas.ApplicationDraftValidate) -> dict[str, str]:
    data = normalize_application_data(payload.data)
    validate_step_payload(payload.step, data)
    return {"status": "ok"}


@app.post("/applications/draft", response_model=schemas.ApplicationRead)
def save_application_draft(payload: schemas.ApplicationDraftSave, db: Session = Depends(get_db)):
    student = crud.get_student(db, payload.student_id)
    if not student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    existing_draft = crud.get_editable_student_application(db, student.id)
    existing_latest = crud.get_latest_student_application(db, student.id)
    if existing_latest and existing_latest.application_status != "Draft" and not existing_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A hostel application already exists for this student.",
        )
    require_admission_open(db, existing_draft=bool(existing_draft))
    data = normalize_application_data(payload.data)
    validate_step_payload(payload.current_step, data)
    session_value = data.get("session")
    if session_value:
        duplicate = crud.get_student_application_for_session(db, student.id, session_value)
        if duplicate and duplicate.application_status != "Draft" and (not existing_draft or duplicate.id != existing_draft.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A hostel application already exists for this session.",
            )
    return save_or_409(lambda: crud.save_application_draft(db, student, payload.current_step, data))


@app.get("/applications/resume/{student_id}", response_model=schemas.ApplicationRead | None)
def resume_application(student_id: int, db: Session = Depends(get_db)):
    if not crud.get_student(db, student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    return crud.get_latest_student_application(db, student_id)


@app.post("/applications/{application_id}/submit", response_model=schemas.ApplicationRead)
def submit_application(application_id: int, payload: schemas.ApplicationDraftSave, db: Session = Depends(get_db)):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    if application.student_id != payload.student_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Application does not belong to this student.")
    if application.application_status != "Draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only draft applications can be submitted.")
    require_admission_open(db, existing_draft=False)
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
    return save_or_409(lambda: crud.submit_application(db, application, data))


@app.get("/applications", response_model=list[schemas.ApplicationRead])
def list_applications(
    status_filter: str | None = None,
    student_id: int | None = None,
    db: Session = Depends(get_db),
):
    return crud.list_applications(db, status=status_filter, student_id=student_id)


@app.get("/applications/{application_id}", response_model=schemas.ApplicationRead)
def get_application(application_id: int, db: Session = Depends(get_db)):
    application = crud.get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
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
def get_application_settings(db: Session = Depends(get_db)):
    return settings_response(crud.get_application_settings(db))


@app.put("/settings/application", response_model=schemas.ApplicationSettingsRead)
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
    if not crud.get_student(db, payload.student_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found.")
    if payload.application_id and not crud.get_application(db, payload.application_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    return save_or_409(lambda: crud.create_payment(db, payload))


def require_successful_payment(payload: schemas.PaymentCreate) -> None:
    if payload.status.lower() not in {"paid", "success", "completed"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Receipt generation requires a successful payment status.",
        )


def require_payment_application(payload: schemas.PaymentCreate, db: Session):
    if not payload.application_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Application ID is required.")
    application = crud.get_application(db, payload.application_id)
    if not application or application.student_id != payload.student_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student application not found.")
    return application


@app.post(
    "/payments/registration/success",
    response_model=schemas.PaymentReceiptRead,
    status_code=status.HTTP_201_CREATED,
)
def registration_payment_success(payload: schemas.PaymentCreate, db: Session = Depends(get_db)):
    require_payment_open(db)
    require_successful_payment(payload)
    application = require_payment_application(payload, db)
    if crud.get_successful_payment_for_application(db, application.id, "Registration Fee"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Registration fee has already been paid for this application.",
        )
    expected = Decimal("100") if application.application_type == "existing" else Decimal("1000")
    if payload.amount != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Registration fee must be Rs. {expected:.2f} for this application type.",
        )
    payment_payload = payload.model_copy(update={"payment_type": "Registration Fee"})
    payment = save_or_409(lambda: crud.create_payment(db, payment_payload))
    return receipt_service.generate_receipt_pdf(db, payment, "application_registration")


@app.post(
    "/payments/hostel/success",
    response_model=schemas.PaymentReceiptRead,
    status_code=status.HTTP_201_CREATED,
)
def hostel_payment_success(payload: schemas.PaymentCreate, db: Session = Depends(get_db)):
    require_payment_open(db)
    require_successful_payment(payload)
    application = require_payment_application(payload, db)
    if crud.get_successful_payment_for_application(db, application.id, "Hostel Admission Fee"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hostel fee has already been paid for this application.",
        )
    if application.status.lower() not in {"selected", "shortlisted", "approved"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hostel receipt requires a shortlisted or approved application.",
        )
    if not application.hostel:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A hostel must be allotted first.")
    hostel_name = application.hostel.name.strip().lower()
    if application.hostel.fee and application.hostel.fee > 0:
        expected = application.hostel.fee
    elif "mahima" in hostel_name:
        expected = Decimal("12000")
    elif "vaidehi" in hostel_name:
        expected = Decimal("10000")
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported hostel fee configuration.")
    if payload.amount != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Hostel fee for {application.hostel.name} must be Rs. {expected:.2f}.",
        )
    payment_payload = payload.model_copy(update={"payment_type": "Hostel Admission Fee"})
    payment = save_or_409(lambda: crud.create_payment(db, payment_payload))
    return receipt_service.generate_receipt_pdf(db, payment, "hostel_admission")


@app.get("/payments", response_model=list[schemas.PaymentRead])
def list_payments(student_id: int | None = None, db: Session = Depends(get_db)):
    return crud.list_payments(db, student_id=student_id)


@app.get("/receipts", response_model=list[schemas.PaymentReceiptRead])
def list_receipts(student_id: int | None = None, db: Session = Depends(get_db)):
    return crud.list_receipts(db, student_id=student_id)


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
        if not application or application.status.lower() not in {"selected", "shortlisted", "approved"}:
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
def get_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = crud.get_receipt(db, receipt_id)
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")
    return receipt


@app.get("/receipts/{receipt_id}/download")
def download_receipt(receipt_id: int, db: Session = Depends(get_db)):
    receipt = crud.get_receipt(db, receipt_id)
    if not receipt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found.")

    # If the receipt has an R2 public URL, redirect to it
    if receipt.pdf_url and receipt.pdf_url.startswith("http"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=receipt.pdf_url, status_code=307)

    # Try to get PDF bytes from R2 or local storage
    pdf_bytes = receipt_service.get_receipt_pdf_bytes(receipt.receipt_number)
    if pdf_bytes:
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{receipt.receipt_number}.pdf"'},
        )

    # Last resort: regenerate the receipt
    if not receipt.payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt PDF not found.")
    updated_receipt = receipt_service.generate_receipt_pdf(db, receipt.payment, receipt.receipt_type)
    if updated_receipt.pdf_url and updated_receipt.pdf_url.startswith("http"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=updated_receipt.pdf_url, status_code=307)

    # Fallback to local file
    path = receipt_service.receipt_pdf_path(receipt.receipt_number)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt PDF not found.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{receipt.receipt_number}.pdf",
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
