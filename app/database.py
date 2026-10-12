import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def make_engine(url: str):
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def ensure_columns(engine) -> None:
    """给已经建好的 SQLite 补上后加的列。新库由 create_all 建齐。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements: list[str] = []
    if "novels" in tables:
        names = {column["name"] for column in inspector.get_columns("novels")}
        if "style" not in names:
            statements.append("ALTER TABLE novels ADD COLUMN style TEXT DEFAULT '' NOT NULL")
    if "jobs" in tables:
        names = {column["name"] for column in inspector.get_columns("jobs")}
        if "line_id" not in names:
            statements.append("ALTER TABLE jobs ADD COLUMN line_id VARCHAR(32)")
    if "characters" in tables:
        names = {column["name"] for column in inspector.get_columns("characters")}
        if "voice_locked" not in names:
            statements.append("ALTER TABLE characters ADD COLUMN voice_locked BOOLEAN DEFAULT 0 NOT NULL")
        if "gender" not in names:
            statements.append("ALTER TABLE characters ADD COLUMN gender VARCHAR(20) DEFAULT '' NOT NULL")
        if "age" not in names:
            statements.append("ALTER TABLE characters ADD COLUMN age VARCHAR(20) DEFAULT '' NOT NULL")
        if "personality" not in names:
            statements.append("ALTER TABLE characters ADD COLUMN personality VARCHAR(20) DEFAULT '' NOT NULL")
    if "script_lines" in tables:
        names = {column["name"] for column in inspector.get_columns("script_lines")}
        if "start_sec" not in names:
            statements.append("ALTER TABLE script_lines ADD COLUMN start_sec FLOAT")
    if "scenes" in tables:
        names = {column["name"] for column in inspector.get_columns("scenes")}
        if "source_url" not in names:
            statements.append("ALTER TABLE scenes ADD COLUMN source_url VARCHAR(1000)")
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory):
    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
