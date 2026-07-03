ALTER TABLE hostel_applications
  ADD COLUMN existing_hostel_name VARCHAR(120) NULL,
  ADD COLUMN existing_room_number VARCHAR(40) NULL,
  ADD COLUMN existing_bed_number VARCHAR(40) NULL,
  ADD COLUMN existing_block VARCHAR(40) NULL,
  ADD COLUMN existing_floor VARCHAR(40) NULL,
  ADD COLUMN existing_previous_session VARCHAR(20) NULL;
