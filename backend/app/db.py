import logging

from sqlalchemy import URL, create_engine, event, inspect, text, update
from sqlalchemy.orm import sessionmaker

from app.config import DB_PATH, ensure_data_dirs, utcnow
from app.models import Base, MasterContractStatus

logger = logging.getLogger(__name__)

engine = create_engine(
    URL.create("sqlite", database=str(DB_PATH)),
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    # WAL lets API reads proceed while the master contract rewrite is in progress.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Columns added after a table first shipped; create_all does not alter existing tables.
_ADDED_COLUMNS = {"agent_message": {"pending_json": "TEXT"}}


def _add_missing_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
                    logger.info("Added column %s.%s", table, name)


def init_db() -> None:
    ensure_data_dirs()
    Base.metadata.create_all(engine)
    _add_missing_columns()
    with SessionLocal.begin() as db:
        status = db.get(MasterContractStatus, 1)
        if status is None:
            db.add(MasterContractStatus(id=1, status="idle", total_records=0, exchanges_json="{}"))
        elif status.status == "downloading":
            # A previous process died mid download; nothing is running now.
            db.execute(
                update(MasterContractStatus)
                .where(MasterContractStatus.id == 1)
                .values(
                    status="error",
                    message="Previous download was interrupted",
                    finished_at=utcnow(),
                )
            )
    logger.info("Database ready at %s", DB_PATH)
