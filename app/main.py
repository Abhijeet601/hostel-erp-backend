from pathlib import Path
from decimal import Decimal
from io import BytesIO

from fastapi import Depends, FastAPI, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.exc import IntegrityError
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
    if crud.get_student_application_for_session(db, payload.student_id, payload.session):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A hostel application already exists for this session.",
        )
    return save_or_409(lambda: crud.create_application(db, payload))


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
