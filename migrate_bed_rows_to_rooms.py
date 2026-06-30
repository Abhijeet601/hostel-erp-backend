import re

from sqlalchemy import create_engine, text
from sqlalchemy import bindparam

from app.config import get_settings


BED_SUFFIX_RE = re.compile(r"^(\d+)[A-C]$", re.IGNORECASE)


def aggregate_status(statuses: list[str]) -> str:
    statuses = [status or "available" for status in statuses]
    if "maintenance" in statuses:
        return "maintenance"
    if statuses and all(status == "occupied" for status in statuses):
        return "occupied"
    if "reserved" in statuses:
        return "reserved"
    return "available"


def main() -> None:
    engine = create_engine(
        get_settings().sqlalchemy_database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10, "read_timeout": 10, "write_timeout": 10},
    )
    converted = 0
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, hostel_id, room_number, floor, building, beds, status
                    FROM rooms
                    ORDER BY hostel_id, floor, room_number
                    """
                )
            ).mappings().all()

            groups: dict[tuple[int, int, str], list[dict]] = {}
            for row in rows:
                match = BED_SUFFIX_RE.match(str(row["room_number"] or ""))
                if not match:
                    continue
                key = (int(row["hostel_id"]), int(row["floor"]), match.group(1))
                groups.setdefault(key, []).append(dict(row))

            for (hostel_id, floor, base_number), group in groups.items():
                if len(group) < 2:
                    continue

                existing = conn.execute(
                    text(
                        """
                        SELECT id
                        FROM rooms
                        WHERE hostel_id = :hostel_id
                          AND floor = :floor
                          AND room_number = :room_number
                        LIMIT 1
                        """
                    ),
                    {"hostel_id": hostel_id, "floor": floor, "room_number": base_number},
                ).scalar()

                keeper = next((item for item in group if str(item["room_number"]).upper().endswith("A")), group[0])
                keeper_id = int(existing or keeper["id"])
                status_value = aggregate_status([str(item["status"] or "available") for item in group])
                building = keeper.get("building")

                if existing:
                    conn.execute(
                        text(
                            """
                            UPDATE rooms
                            SET beds = :beds, status = :status, building = :building
                            WHERE id = :id
                            """
                        ),
                        {"id": keeper_id, "beds": len(group), "status": status_value, "building": building},
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE rooms
                            SET room_number = :room_number, beds = :beds, status = :status
                            WHERE id = :id
                            """
                        ),
                        {"id": keeper_id, "room_number": base_number, "beds": len(group), "status": status_value},
                    )

                duplicate_ids = [int(item["id"]) for item in group if int(item["id"]) != keeper_id]
                if duplicate_ids:
                    conn.execute(
                        text("UPDATE hostel_applications SET room_id = :keeper_id WHERE room_id IN :duplicate_ids")
                        .bindparams(bindparam("duplicate_ids", expanding=True)),
                        {"keeper_id": keeper_id, "duplicate_ids": tuple(duplicate_ids)},
                    )
                    conn.execute(
                        text("DELETE FROM rooms WHERE id IN :duplicate_ids")
                        .bindparams(bindparam("duplicate_ids", expanding=True)),
                        {"duplicate_ids": tuple(duplicate_ids)},
                    )
                converted += 1
    finally:
        engine.dispose()

    print(f"Converted {converted} room groups to 3-bed rooms.")


if __name__ == "__main__":
    main()
