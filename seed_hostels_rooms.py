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
            for suffix in ("A", "B", "C"):
                rooms.append(
                    {
                        "room_number": f"{floor}{room:02d}{suffix}",
                        "floor": floor,
                        "building": "Mahima",
                    }
                )
    return rooms


def vaidehi_rooms() -> list[dict[str, object]]:
    rooms = []
    for room in range(1, 54):
        for suffix in ("A", "B", "C"):
            rooms.append(
                {
                    "room_number": f"{room}{suffix}",
                    "floor": 1,
                    "building": "Vaidehi",
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
    conn.execute(
        text(
            """
            INSERT INTO rooms (hostel_id, room_number, floor, building, beds, status)
            VALUES (:hostel_id, :room_number, :floor, :building, 1, 'available')
            ON DUPLICATE KEY UPDATE
              floor = VALUES(floor),
              building = VALUES(building),
              beds = VALUES(beds),
              status = VALUES(status)
            """
        ),
        [{"hostel_id": hostel_id, **room} for room in rooms],
    )


def main() -> None:
    engine = create_engine(get_settings().database_url)
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

    for name, room_count in counts:
        print(f"{name}: {room_count} rooms")


if __name__ == "__main__":
    main()
