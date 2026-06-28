INSERT INTO hostels (name, capacity, fee, floors)
VALUES
  ('Mahima', 504, 0.00, 7),
  ('Vaidehi', 159, 0.00, 1)
ON DUPLICATE KEY UPDATE
  capacity = VALUES(capacity),
  floors = VALUES(floors);

DROP PROCEDURE IF EXISTS seed_hostel_rooms;

DELIMITER //

CREATE PROCEDURE seed_hostel_rooms()
BEGIN
  DECLARE mahima_id INT;
  DECLARE vaidehi_id INT;
  DECLARE current_floor INT DEFAULT 1;
  DECLARE current_room INT;
  DECLARE suffix_index INT;
  DECLARE suffix_value CHAR(1);

  SELECT id INTO mahima_id FROM hostels WHERE name = 'Mahima';
  SELECT id INTO vaidehi_id FROM hostels WHERE name = 'Vaidehi';

  WHILE current_floor <= 7 DO
    SET current_room = 1;

    WHILE current_room <= 24 DO
      SET suffix_index = 1;

      WHILE suffix_index <= 3 DO
        SET suffix_value = CASE suffix_index
          WHEN 1 THEN 'A'
          WHEN 2 THEN 'B'
          ELSE 'C'
        END;

        INSERT INTO rooms (hostel_id, room_number, floor, building, beds, status)
        VALUES (
          mahima_id,
          CONCAT(current_floor, LPAD(current_room, 2, '0'), suffix_value),
          current_floor,
          'Mahima',
          1,
          'available'
        )
        ON DUPLICATE KEY UPDATE
          floor = VALUES(floor),
          building = VALUES(building),
          beds = VALUES(beds),
          status = VALUES(status);

        SET suffix_index = suffix_index + 1;
      END WHILE;

      SET current_room = current_room + 1;
    END WHILE;

    SET current_floor = current_floor + 1;
  END WHILE;

  SET current_room = 1;

  WHILE current_room <= 53 DO
    SET suffix_index = 1;

    WHILE suffix_index <= 3 DO
      SET suffix_value = CASE suffix_index
        WHEN 1 THEN 'A'
        WHEN 2 THEN 'B'
        ELSE 'C'
      END;

      INSERT INTO rooms (hostel_id, room_number, floor, building, beds, status)
      VALUES (
        vaidehi_id,
        CONCAT(current_room, suffix_value),
        1,
        'Vaidehi',
        1,
        'available'
      )
      ON DUPLICATE KEY UPDATE
        floor = VALUES(floor),
        building = VALUES(building),
        beds = VALUES(beds),
        status = VALUES(status);

      SET suffix_index = suffix_index + 1;
    END WHILE;

    SET current_room = current_room + 1;
  END WHILE;
END//

DELIMITER ;

CALL seed_hostel_rooms();

DROP PROCEDURE seed_hostel_rooms;
