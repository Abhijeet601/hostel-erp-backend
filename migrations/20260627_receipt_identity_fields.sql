ALTER TABLE hostel_applications
  ADD COLUMN blood_group VARCHAR(10) NULL AFTER correspondence_address,
  ADD COLUMN aadhar_number VARCHAR(12) NULL AFTER blood_group,
  ADD COLUMN religion VARCHAR(60) NULL AFTER aadhar_number,
  ADD COLUMN nationality VARCHAR(60) NULL AFTER religion,
  ADD COLUMN student_photo_data MEDIUMTEXT NULL AFTER nationality;
