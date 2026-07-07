from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


settings = get_settings()

engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}
if not settings.sqlalchemy_database_url.startswith("sqlite"):
    engine_options.update(
        {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 30,
        }
    )

engine = create_engine(settings.sqlalchemy_database_url, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
