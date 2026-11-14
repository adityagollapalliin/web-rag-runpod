from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Parent of every ORM model; Alembic reads its metadata."""
