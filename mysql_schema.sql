CREATE TABLE IF NOT EXISTS students (
  id INT AUTO_INCREMENT PRIMARY KEY,
  student_code VARCHAR(32) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(160) NOT NULL UNIQUE,
  mobile VARCHAR(20) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
  reset_token_hash VARCHAR(255) NULL,
  reset_token_expires_at DATETIME NULL,
  reset_requested_at DATETIME NULL,
  reset_attempt_count INT NOT NULL DEFAULT 0,
  reset_last_attempt_at DATETIME NULL,
  date_of_birth DATE NULL,
  gender VARCHAR(20) NULL,
  category VARCHAR(20) NULL,
  course VARCHAR(80) NULL,
  session VARCHAR(20) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hostels (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL UNIQUE,
  warden VARCHAR(120) NULL,
  capacity INT NOT NULL DEFAULT 0,
  fee DECIMAL(10,2) NOT NULL DEFAULT 0,
  floors INT NOT NULL DEFAULT 1,
  established INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rooms (
  id INT AUTO_INCREMENT PRIMARY KEY,
  hostel_id INT NOT NULL,
  room_number VARCHAR(20) NOT NULL,
  floor INT NOT NULL,
  building VARCHAR(80) NULL,
  beds INT NOT NULL DEFAULT 1,
  status VARCHAR(30) NOT NULL DEFAULT 'available',
  UNIQUE KEY uq_room_hostel_number (hostel_id, room_number),
  CONSTRAINT fk_rooms_hostel FOREIGN KEY (hostel_id) REFERENCES hostels(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS hostel_applications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  application_no VARCHAR(40) NOT NULL UNIQUE,
  student_id INT NOT NULL,
  application_type VARCHAR(30) NOT NULL DEFAULT 'new',
  admission_level VARCHAR(2) NULL,
  admission_id VARCHAR(50) NULL,
  college_name VARCHAR(160) NULL,
  course VARCHAR(80) NULL,
  session VARCHAR(20) NULL,
  father_name VARCHAR(120) NULL,
  mother_name VARCHAR(120) NULL,
  guardian_name VARCHAR(120) NULL,
  guardian_mobile VARCHAR(20) NULL,
  permanent_address TEXT NULL,
  correspondence_address TEXT NULL,
  blood_group VARCHAR(10) NULL,
  aadhar_number VARCHAR(12) NULL,
  religion VARCHAR(60) NULL,
  nationality VARCHAR(60) NULL,
  student_photo_data LONGTEXT NULL,
  aadhar_card_data LONGTEXT NULL,
  admission_receipt_data LONGTEXT NULL,
  income_certificate_data LONGTEXT NULL,
  caste_certificate_data LONGTEXT NULL,
  intermediate_college VARCHAR(160) NULL,
  board VARCHAR(50) NULL,
  previous_course VARCHAR(80) NULL,
  result_type VARCHAR(30) NULL,
  marks_obtained DECIMAL(8,2) NULL,
  total_marks DECIMAL(8,2) NULL,
  percentage DECIMAL(5,2) NULL,
  roll_number VARCHAR(50) NULL,
  subject VARCHAR(80) NULL,
  applied_category VARCHAR(20) NULL,
  allotted_category VARCHAR(20) NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'Draft',
  application_status VARCHAR(30) NOT NULL DEFAULT 'Draft',
  current_step INT NOT NULL DEFAULT 1,
  last_saved_at DATETIME NULL,
  submitted_at DATETIME NULL,
  merit_rank INT NULL,
  hostel_id INT NULL,
  room_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_applications_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
  CONSTRAINT fk_applications_hostel FOREIGN KEY (hostel_id) REFERENCES hostels(id) ON DELETE SET NULL,
  CONSTRAINT fk_applications_room FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admission_payment_settings (
  id INT PRIMARY KEY,
  admission_start_date DATE NULL,
  admission_end_date DATE NULL,
  payment_start_date DATE NULL,
  payment_end_date DATE NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  transaction_no VARCHAR(50) NOT NULL UNIQUE,
  student_id INT NOT NULL,
  application_id INT NULL,
  payment_type VARCHAR(50) NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  mode VARCHAR(255) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'Pending',
  paid_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_payments_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
  CONSTRAINT fk_payments_application FOREIGN KEY (application_id) REFERENCES hostel_applications(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS payment_receipts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  receipt_number VARCHAR(50) NOT NULL UNIQUE,
  application_number VARCHAR(40) NULL,
  student_id INT NOT NULL,
  receipt_type VARCHAR(40) NOT NULL,
  payment_id INT NULL,
  hostel_name VARCHAR(120) NULL,
  room_number VARCHAR(20) NULL,
  amount DECIMAL(10,2) NOT NULL,
  transaction_id VARCHAR(50) NULL,
  pdf_url VARCHAR(255) NULL,
  qr_code VARCHAR(255) NULL,
  generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_payment_receipts_application_number (application_number),
  KEY ix_payment_receipts_student_id (student_id),
  KEY ix_payment_receipts_receipt_type (receipt_type),
  KEY ix_payment_receipts_payment_id (payment_id),
  KEY ix_payment_receipts_transaction_id (transaction_id),
  CONSTRAINT fk_receipts_student FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
  CONSTRAINT fk_receipts_payment FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS admin_users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(80) NOT NULL UNIQUE,
  email VARCHAR(160) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  full_name VARCHAR(120) NOT NULL,
  role VARCHAR(50) NOT NULL DEFAULT 'admin',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS activity_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  entity_type VARCHAR(50) NOT NULL,
  entity_id VARCHAR(50) NOT NULL,
  action VARCHAR(80) NOT NULL,
  old_values TEXT NULL,
  new_values TEXT NULL,
  admin_id INT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_activity_logs_entity_type (entity_type),
  KEY ix_activity_logs_entity_id (entity_id),
  KEY ix_activity_logs_action (action),
  CONSTRAINT fk_activity_logs_admin FOREIGN KEY (admin_id) REFERENCES admin_users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
