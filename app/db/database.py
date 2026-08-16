from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

# `check_same_thread` es exclusivo de SQLite: pasárselo a psycopg2 lanza
# excepción al crear el engine. Se detecta el motor desde la URL para que el
# mismo código sirva con SQLite en local y con Postgres (Supabase) en producción.
ES_SQLITE = settings.DATABASE_URL.startswith("sqlite")

if ES_SQLITE:
    engine = create_engine(
        settings.DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # SQLAlchemy 2.0 dropped support for the deprecated 'postgres://' scheme
    # but many providers like Render/Heroku/Supabase still inject it via env vars.
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # pool_pre_ping evita usar conexiones que el pooler de Supabase ya cerró
    # por inactividad, algo habitual en instancias que se duermen (Render free).
    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=300,
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
    Migración mínima e idempotente para columnas nuevas en bases de datos ya
    existentes (create_all no altera tablas ya creadas).

    Funciona tanto en SQLite como en Postgres. La sintaxis de los DEFAULT difiere
    entre motores: SQLite acepta 0 como booleano y un literal de texto para JSON,
    Postgres exige FALSE y un cast explícito.
    """
    # Literales de DEFAULT dependientes del motor.
    # En Postgres, un string literal válido como JSON se convierte automáticamente,
    # no hace falta el cast ::json explícito que a veces falla en ALTER TABLE.
    default_json = "'[]'"
    default_false = "0" if ES_SQLITE else "FALSE"

    inspector = inspect(engine)
    if "historial_interacciones" not in inspector.get_table_names():
        return

    def _agregar_columna(tabla: str, columna: str, definicion: str):
        """Agrega la columna solo si falta. No interrumpe el arranque si falla."""
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"))
        except Exception as e:
            print(f"[MIGRACION] No se pudo agregar {tabla}.{columna}: {e}")

    columnas = {c["name"] for c in inspector.get_columns("historial_interacciones")}
    if "historial_conversacion" not in columnas:
        _agregar_columna(
            "historial_interacciones", "historial_conversacion", f"JSON DEFAULT {default_json}"
        )
    if "en_atencion_humana" not in columnas:
        _agregar_columna(
            "historial_interacciones", "en_atencion_humana", f"BOOLEAN DEFAULT {default_false}"
        )

    if "audit_log" in inspector.get_table_names():
        columnas_audit = {c["name"] for c in inspector.get_columns("audit_log")}
        if "handoff_context" not in columnas_audit:
            _agregar_columna("audit_log", "handoff_context", "JSON")
        if "atendido" not in columnas_audit:
            _agregar_columna("audit_log", "atendido", f"BOOLEAN DEFAULT {default_false}")

    if "cuarentena_casos" in inspector.get_table_names():
        columnas_cuarentena = {c["name"] for c in inspector.get_columns("cuarentena_casos")}
        if "folio" not in columnas_cuarentena:
            _agregar_columna("cuarentena_casos", "folio", "VARCHAR(30)")
