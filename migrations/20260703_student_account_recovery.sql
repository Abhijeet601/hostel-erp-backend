ALTER TABLE students
  ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN force_password_change BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN reset_token_hash VARCHAR(255) NULL,
  ADD COLUMN reset_token_expires_at DATETIME NULL,
  ADD COLUMN reset_requested_at DATETIME NULL,
  ADD COLUMN reset_attempt_count INT NOT NULL DEFAULT 0,
  ADD COLUMN reset_last_attempt_at DATETIME NULL;

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
