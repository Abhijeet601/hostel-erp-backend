ALTER TABLE hostel_applications
  ADD COLUMN admission_level VARCHAR(2) NULL AFTER application_type,
  ADD COLUMN college_name VARCHAR(160) NULL AFTER admission_id,
  ADD COLUMN course VARCHAR(80) NULL AFTER college_name,
  ADD COLUMN session VARCHAR(20) NULL AFTER course,
  ADD COLUMN previous_course VARCHAR(80) NULL AFTER board,
  ADD COLUMN result_type VARCHAR(30) NULL AFTER previous_course;
