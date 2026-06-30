from sqlalchemy import create_engine, text

from app.config import get_settings


HOSTELS = [
    {"name": "Mahima", "capacity": 504, "floors": 7, "fee": 12000.00},
    {"name": "Vaidehi", "capacity": 159, "floors": 1, "fee": 10000.00},
]


def mahima_rooms() -> list[dict[str, object]]:
    rooms = []
    for floor in range(1, 8):
        for room in range(1, 25):
            rooms.append(
                {
                    "room_number": f"{floor}{room:02d}",
                    "floor": floor,
                    "building": "Mahima",
                    "beds": 3,
                }
            )
    return rooms


def vaidehi_rooms() -> list[dict[str, object]]:
    rooms = []
    for room in range(1, 54):
        rooms.append(
            {
                "room_number": f"{room}",
                "floor": 1,
                "building": "Vaidehi",
                "beds": 3,
            }
        )
    return rooms


def upsert_hostel(conn, hostel: dict[str, object]) -> int:
    conn.execute(
        text(
            """
            INSERT INTO hostels (name, capacity, fee, floors)
            VALUES (:name, :capacity, :fee, :floors)
            ON DUPLICATE KEY UPDATE
              capacity = VALUES(capacity),
              fee = VALUES(fee),
              floors = VALUES(floors)
            """
        ),
        hostel,
    )
    return conn.execute(text("SELECT id FROM hostels WHERE name = :name"), hostel).scalar_one()


def upsert_rooms(conn, hostel_id: int, rooms: list[dict[str, object]]) -> None:
    existing_count = conn.execute(
        text("SELECT COUNT(*) FROM rooms WHERE hostel_id = :hostel_id"),
        {"hostel_id": hostel_id},
    ).scalar_one()
    if existing_count == len(rooms):
        return

    statement = text(
        """
            INSERT INTO rooms (hostel_id, room_number, floor, building, beds, status)
            VALUES (:hostel_id, :room_number, :floor, :building, :beds, 'available')
            ON DUPLICATE KEY UPDATE
              floor = VALUES(floor),
              building = VALUES(building),
              beds = VALUES(beds),
              status = VALUES(status)
            """
    )
    rows = [{"hostel_id": hostel_id, **room} for room in rooms]
    for start in range(0, len(rows), 50):
        conn.execute(statement, rows[start : start + 50])


def main() -> None:
    engine = create_engine(
        get_settings().sqlalchemy_database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "read_timeout": 10, "write_timeout": 10},
    )
    try:
        with engine.begin() as conn:
            hostel_ids = {hostel["name"]: upsert_hostel(conn, hostel) for hostel in HOSTELS}
            upsert_rooms(conn, hostel_ids["Mahima"], mahima_rooms())
            upsert_rooms(conn, hostel_ids["Vaidehi"], vaidehi_rooms())

            counts = conn.execute(
                text(
                    """
                    SELECT h.name, COUNT(r.id) AS rooms
                    FROM hostels h
                    LEFT JOIN rooms r ON r.hostel_id = h.id
                    WHERE h.name IN ('Mahima', 'Vaidehi')
                    GROUP BY h.id, h.name
                    ORDER BY h.name
                    """
                )
            ).all()
    finally:
        engine.dispose()

    for name, room_count in counts:
        print(f"{name}: {room_count} rooms")


if __name__ == "__main__":
    main()
