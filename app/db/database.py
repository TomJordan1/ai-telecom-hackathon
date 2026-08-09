from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_lightweight_migrations():
    """
    Migración mínima e idempotente para columnas nuevas en bases de datos SQLite
    ya existentes (create_all no altera tablas ya creadas).
    """
    inspector = inspect(engine)
    if "historial_interacciones" not in inspector.get_table_names():
        return

    columnas = {c["name"] for c in inspector.get_columns("historial_interacciones")}
    if "historial_conversacion" not in columnas:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE historial_interacciones "
                "ADD COLUMN historial_conversacion JSON DEFAULT '[]'"
            ))

    if "audit_log" in inspector.get_table_names():
        columnas_audit = {c["name"] for c in inspector.get_columns("audit_log")}
        if "handoff_context" not in columnas_audit:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE audit_log ADD COLUMN handoff_context JSON"))
        if "atendido" not in columnas_audit:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE audit_log ADD COLUMN atendido BOOLEAN DEFAULT 0"))
