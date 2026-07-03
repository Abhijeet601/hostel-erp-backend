from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    student_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    mobile: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    force_password_change: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_token_hash: Mapped[str | None] = mapped_column(String(255), index=True)
    reset_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    reset_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    reset_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    reset_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[str | None] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(20))
    course: Mapped[str | None] = mapped_column(String(80))
    session: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    applications: Mapped[list["HostelApplication"]] = relationship(back_populates="student")
    payments: Mapped[list["Payment"]] = relationship(back_populates="student")
    receipts: Mapped[list["PaymentReceipt"]] = relationship(back_populates="student")


class Hostel(Base):
    __tablename__ = "hostels"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    warden: Mapped[str | None] = mapped_column(String(120))
    capacity: Mapped[int] = mapped_column(default=0)
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    floors: Mapped[int] = mapped_column(default=1)
    established: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    rooms: Mapped[list["Room"]] = relationship(back_populates="hostel")
    applications: Mapped[list["HostelApplication"]] = relationship(back_populates="hostel")


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("hostel_id", "room_number", name="uq_room_hostel_number"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    hostel_id: Mapped[int] = mapped_column(ForeignKey("hostels.id", ondelete="CASCADE"), index=True)
    room_number: Mapped[str] = mapped_column(String(20), index=True)
    floor: Mapped[int] = mapped_column()
    building: Mapped[str | None] = mapped_column(String(80))
    beds: Mapped[int] = mapped_column(default=3)
    occupied_beds: Mapped[int] = mapped_column(default=0)
    available_beds: Mapped[int] = mapped_column(default=3)
    status: Mapped[str] = mapped_column(String(30), default="available")

    hostel: Mapped[Hostel] = relationship(back_populates="rooms")
    applications: Mapped[list["HostelApplication"]] = relationship(back_populates="room")


class HostelApplication(Base):
    __tablename__ = "hostel_applications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    application_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    application_type: Mapped[str] = mapped_column(String(30), default="new")
    admission_level: Mapped[str | None] = mapped_column(String(2))
    admission_id: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    college_name: Mapped[str | None] = mapped_column(String(160))
    course: Mapped[str | None] = mapped_column(String(80))
    session: Mapped[str | None] = mapped_column(String(20))
    father_name: Mapped[str | None] = mapped_column(String(120))
    mother_name: Mapped[str | None] = mapped_column(String(120))
    guardian_name: Mapped[str | None] = mapped_column(String(120))
    guardian_mobile: Mapped[str | None] = mapped_column(String(20))
    permanent_address: Mapped[str | None] = mapped_column(Text)
    correspondence_address: Mapped[str | None] = mapped_column(Text)
    blood_group: Mapped[str | None] = mapped_column(String(10))
    aadhar_number: Mapped[str | None] = mapped_column(String(12))
    religion: Mapped[str | None] = mapped_column(String(60))
    nationality: Mapped[str | None] = mapped_column(String(60))
    student_photo_data: Mapped[str | None] = mapped_column(LONGTEXT)
    intermediate_college: Mapped[str | None] = mapped_column(String(160))
    board: Mapped[str | None] = mapped_column(String(50))
    previous_course: Mapped[str | None] = mapped_column(String(80))
    result_type: Mapped[str | None] = mapped_column(String(30))
    marks_obtained: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    total_marks: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    roll_number: Mapped[str | None] = mapped_column(String(50))
    subject: Mapped[str | None] = mapped_column(String(80))
    applied_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    allotted_category: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="Draft")
    application_status: Mapped[str] = mapped_column(String(30), default="Draft", index=True)
    current_step: Mapped[int] = mapped_column(default=1)
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    merit_rank: Mapped[int | None] = mapped_column()
    hostel_id: Mapped[int | None] = mapped_column(ForeignKey("hostels.id", ondelete="SET NULL"))
    room_id: Mapped[int | None] = mapped_column(ForeignKey("rooms.id", ondelete="SET NULL"))
    block: Mapped[str | None] = mapped_column(String(40))
    floor: Mapped[str | None] = mapped_column(String(20))
    bed: Mapped[str | None] = mapped_column(String(20))
    allocation_date: Mapped[date | None] = mapped_column(Date)
    allocation_status: Mapped[str] = mapped_column(String(30), default="allocated")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    student: Mapped[Student] = relationship(back_populates="applications")
    hostel: Mapped[Hostel | None] = relationship(back_populates="applications")
    room: Mapped[Room | None] = relationship(back_populates="applications")
    payments: Mapped[list["Payment"]] = relationship(back_populates="application")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    transaction_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("hostel_applications.id", ondelete="SET NULL"))
    payment_type: Mapped[str] = mapped_column(String(50))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    mode: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="Pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped[Student] = relationship(back_populates="payments")
    application: Mapped[HostelApplication | None] = relationship(back_populates="payments")
    receipts: Mapped[list["PaymentReceipt"]] = relationship(back_populates="payment")


class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    receipt_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    application_number: Mapped[str | None] = mapped_column(String(40), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    receipt_type: Mapped[str] = mapped_column(String(40), index=True)
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="SET NULL"), index=True)
    hostel_name: Mapped[str | None] = mapped_column(String(120))
    room_number: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    transaction_id: Mapped[str | None] = mapped_column(String(50), index=True)
    pdf_url: Mapped[str | None] = mapped_column(String(255))
    qr_code: Mapped[str | None] = mapped_column(String(255))
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped[Student] = relationship(back_populates="receipts")
    payment: Mapped[Payment | None] = relationship(back_populates="receipts")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(50), default="admin")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    old_values: Mapped[str | None] = mapped_column(Text)
    new_values: Mapped[str | None] = mapped_column(Text)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AdmissionPaymentSettings(Base):
    __tablename__ = "admission_payment_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    admission_start_date: Mapped[date | None] = mapped_column(Date)
    admission_end_date: Mapped[date | None] = mapped_column(Date)
    payment_start_date: Mapped[date | None] = mapped_column(Date)
    payment_end_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
