from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StudentBase(BaseModel):
    name: str = Field(..., max_length=120)
    email: EmailStr
    mobile: str = Field(..., pattern=r"^\d{10}$")
    date_of_birth: date | None = None
    gender: str | None = None
    category: str | None = None
    course: str | None = None
    session: str | None = None


class StudentCreate(StudentBase):
    student_code: str = Field(..., max_length=32)


class StudentRegister(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    mobile: str = Field(..., min_length=10, max_length=20)
    date_of_birth: date
    password: str = Field(..., min_length=8, max_length=128)


class StudentUpdate(BaseModel):
    student_code: str | None = Field(None, max_length=32)
    name: str | None = Field(None, max_length=120)
    email: EmailStr | None = None
    mobile: str | None = Field(None, pattern=r"^\d{10}$")
    date_of_birth: date | None = None
    gender: str | None = None
    category: str | None = None
    course: str | None = None
    session: str | None = None
    is_active: bool | None = None
    force_password_change: bool | None = None


class StudentPasswordUpdate(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)


class StudentForgotPasswordRequest(BaseModel):
    email: EmailStr
    aadhaar_number: str = Field(..., pattern=r"^\d{12}$")


class StudentCompletePasswordResetRequest(BaseModel):
    token: str = Field(..., min_length=16, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)


class AdminStudentAccountUpdate(BaseModel):
    application_number: str | None = Field(None, min_length=3, max_length=32)
    student_code: str | None = Field(None, min_length=3, max_length=32)
    name: str | None = Field(None, max_length=120)
    email: EmailStr | None = None
    mobile_number: str | None = Field(None, pattern=r"^\d{10}$")
    mobile: str | None = Field(None, pattern=r"^\d{10}$")
    aadhaar_number: str | None = Field(None, pattern=r"^\d{12}$")
    aadhar_number: str | None = Field(None, pattern=r"^\d{12}$")
    course_name: str | None = Field(None, max_length=80)
    course: str | None = Field(None, max_length=80)
    session: str | None = Field(None, max_length=20)
    is_active: bool | None = None
    force_password_change: bool | None = None


class AdminStudentPasswordReset(BaseModel):
    password: str | None = Field(None, min_length=8, max_length=128)
    generate_temporary: bool = True
    force_password_change: bool = True
    send_email: bool = False


class AccountActionResponse(BaseModel):
    message: str
    temporary_password: str | None = None
    email_status: str | None = None


class StudentRead(StudentBase):
    id: int
    student_code: str
    created_at: datetime
    updated_at: datetime
    is_active: bool = True
    force_password_change: bool = False

    model_config = ConfigDict(from_attributes=True)


class HostelBase(BaseModel):
    name: str
    warden: str | None = None
    capacity: int = 0
    fee: Decimal = Decimal("0.00")
    floors: int = 1
    established: int | None = None


class HostelCreate(HostelBase):
    pass


class HostelUpdate(BaseModel):
    name: str | None = None
    warden: str | None = None
    capacity: int | None = None
    fee: Decimal | None = None
    floors: int | None = None
    established: int | None = None


class HostelRead(HostelBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoomBase(BaseModel):
    hostel_id: int
    room_number: str
    floor: int
    building: str | None = None
    beds: int = 3
    occupied_beds: int = 0
    available_beds: int = 3
    status: str = "available"


class RoomCreate(RoomBase):
    pass


class RoomUpdate(BaseModel):
    status: Literal["available", "occupied", "reserved", "maintenance"] | None = None
    beds: int | None = None
    building: str | None = None
    occupied_beds: int | None = None
    available_beds: int | None = None


class RoomRead(RoomBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


ApplicationStatus = Literal["Draft", "Submitted", "Verified", "Approved", "Rejected", "Shortlisted", "Selected", "Waitlisted", "Pending"]


class ApplicationBase(BaseModel):
    student_id: int
    application_type: str = "new"
    admission_level: Literal["UG", "PG"] | None = None
    admission_id: str | None = None
    college_name: str | None = None
    course: str | None = None
    session: str | None = None
    father_name: str | None = None
    mother_name: str | None = None
    guardian_name: str | None = None
    guardian_mobile: str | None = None
    permanent_address: str | None = None
    correspondence_address: str | None = None
    blood_group: str | None = None
    aadhar_number: str | None = Field(None, pattern=r"^\d{12}$")
    religion: str | None = None
    nationality: str | None = None
    student_photo_data: str | None = None
    aadhar_card_data: str | None = None
    admission_receipt_data: str | None = None
    income_certificate_data: str | None = None
    caste_certificate_data: str | None = None
    intermediate_college: str | None = None
    board: str | None = None
    previous_course: str | None = None
    result_type: str | None = None
    marks_obtained: Decimal | None = None
    total_marks: Decimal | None = None
    percentage: Decimal | None = None
    roll_number: str | None = None
    subject: str | None = None
    applied_category: Literal["UR", "BC", "EBC", "EWS", "SC", "ST"] | None = None
    allotted_category: Literal["UR", "BC", "EBC", "EWS", "SC", "ST"] | None = None
    hostel_id: int | None = None
    room_id: int | None = None
    block: str | None = None
    floor: str | None = None
    bed: str | None = None
    allocation_date: date | None = None
    allocation_status: str | None = None


class ApplicationCreate(ApplicationBase):
    application_no: str


class AllocationRequest(BaseModel):
    room_id: int
    bed: str
    hostel_id: int | None = None
    block: str | None = None
    floor: str | None = None
    allocation_date: date | None = None
    allocation_status: str | None = "allocated"


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus
    allotted_category: Literal["UR", "BC", "EBC", "EWS", "SC", "ST"] | None = None
    merit_rank: int | None = None
    hostel_id: int | None = None
    room_id: int | None = None
    block: str | None = None
    floor: str | None = None
    bed: str | None = None
    allocation_date: date | None = None
    allocation_status: str | None = None


class ApplicationRead(ApplicationBase):
    id: int
    application_no: str
    status: str
    application_status: str = "Draft"
    current_step: int = 1
    last_saved_at: datetime | None = None
    submitted_at: datetime | None = None
    merit_rank: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationDraftSave(BaseModel):
    student_id: int
    current_step: int = Field(..., ge=1, le=8)
    data: dict[str, Any] = Field(default_factory=dict)


class ApplicationDraftValidate(BaseModel):
    step: int = Field(..., ge=1, le=8)
    data: dict[str, Any] = Field(default_factory=dict)


class ApplicationSettingsRead(BaseModel):
    admission_start_date: date | None = None
    admission_end_date: date | None = None
    payment_start_date: date | None = None
    payment_end_date: date | None = None
    admission_state: str
    payment_state: str
    admission_message: str | None = None
    payment_message: str | None = None


class ApplicationSettingsUpdate(BaseModel):
    admission_start_date: date | None = None
    admission_end_date: date | None = None
    payment_start_date: date | None = None
    payment_end_date: date | None = None


class AdminDashboardMetrics(BaseModel):
    settings: ApplicationSettingsRead
    countdown_to_admission_closing: str | None = None
    countdown_to_payment_closing: str | None = None
    total_draft_applications: int
    total_submitted_applications: int
    total_approved_applications: int
    total_rejected_applications: int
    total_rooms: int = 0
    occupied_rooms: int = 0
    available_rooms: int = 0
    total_beds: int = 0
    occupied_beds: int = 0
    available_beds: int = 0
    hostel_occupancy_pct: float = 0.0
    recent_allocations: list[dict[str, Any]] = []
    recent_vacated_beds: list[dict[str, Any]] = []
    room_status_summary: dict[str, int] = {}


class PaymentBase(BaseModel):
    student_id: int
    application_id: int | None = None
    payment_type: str
    amount: Decimal
    mode: str
    status: str = "Pending"
    paid_at: datetime | None = None


class PaymentCreate(PaymentBase):
    transaction_no: str


class PaymentInitiateRequest(BaseModel):
    student_id: int
    application_id: int
    payment_type: str
    amount: Decimal


class PaymentInitiateResponse(BaseModel):
    gateway_url: str
    encRequest: str
    access_code: str


class PaymentRead(PaymentBase):
    id: int
    transaction_no: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentReceiptRead(BaseModel):
    id: int
    receipt_number: str
    application_number: str | None
    student_id: int
    receipt_type: str
    payment_id: int | None
    hostel_name: str | None
    room_number: str | None
    amount: Decimal
    transaction_id: str | None
    pdf_url: str | None
    qr_code: str | None
    generated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReceiptGenerateRequest(BaseModel):
    payment_id: int
    receipt_type: str | None = None


class AdminCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=80)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., max_length=120)
    role: Literal["admin", "super_admin"] = "admin"
    is_active: bool = True


class AdminRead(BaseModel):
    id: int
    username: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=160)
    password: str = Field(..., min_length=1, max_length=128)
    role: str = "auto"


class LoginResponse(BaseModel):
    role: str
    user: AdminRead | StudentRead


class FileUploadResponse(BaseModel):
    url: str
    key: str
    content_type: str
    size: int


class ReceiptDownloadInfo(BaseModel):
    receipt_number: str
    pdf_url: str | None
    download_url: str
