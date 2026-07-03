ALTER TABLE payments
  ADD COLUMN currency VARCHAR(10) NOT NULL DEFAULT 'INR',
  ADD COLUMN tracking_id VARCHAR(80) NULL,
  ADD COLUMN bank_ref_no VARCHAR(80) NULL,
  ADD COLUMN failure_reason VARCHAR(255) NULL,
  ADD COLUMN sub_account_id VARCHAR(80) NULL,
  ADD COLUMN gateway_response TEXT NULL;

CREATE INDEX ix_payments_tracking_id ON payments (tracking_id);
CREATE INDEX ix_payments_bank_ref_no ON payments (bank_ref_no);
CREATE INDEX ix_payments_sub_account_id ON payments (sub_account_id);
