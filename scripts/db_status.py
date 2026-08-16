"""
db_status.py
------------
Inspección rápida del estado de la base operacional (Supabase Postgres o SQLite):
qué tablas existen y cuántas filas tiene cada una.

Útil antes y después de una reingesta para verificar qué se cargó realmente y
detectar tablas obsoletas que quedaron de un esquema anterior.

    python scripts/db_status.py
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, text  # noqa: E402

from app.db.database import Base, engine  # noqa: E402
from app.db import models  # noqa: F401,E402  (registra los modelos en Base.metadata)


def main():
    inspector = inspect(engine)
    existentes = sorted(inspector.get_table_names())
    # documentos_politicas no la define ningún modelo de SQLAlchemy a propósito:
    # es la tabla vectorial del RAG, creada por scripts/setup_supabase.sql y
    # poblada por scripts/ingest_supabase.py. No es obsoleta.
    esperadas = set(Base.metadata.tables.keys()) | {"documentos_politicas"}

    print("=" * 70)
    print(f"  Estado de la base  ->  motor: {engine.dialect.name}")
    print("=" * 70)
    print(f"Tablas en la base: {len(existentes)}")

    with engine.connect() as conn:
        for tabla in existentes:
            try:
                filas = conn.execute(text(f'SELECT COUNT(*) FROM "{tabla}"')).scalar()
            except Exception as e:
                filas = f"error: {e}"
            marca = " " if tabla in esperadas else "!"
            print(f"  {marca} {tabla:32} {filas}")

    obsoletas = [t for t in existentes if t not in esperadas]
    faltantes = sorted(esperadas - set(existentes))

    if obsoletas:
        print("\nTablas obsoletas (marcadas con '!', ya no las define ningún modelo):")
        for tabla in obsoletas:
            print(f"  - {tabla}")
        print("  Se pueden eliminar con: python scripts/reset_db.py --drop-obsoletas")

    if faltantes:
        print("\nTablas que definen los modelos pero faltan en la base:")
        for tabla in faltantes:
            print(f"  - {tabla}")
        print("  Se crean al ejecutar: python scripts/ingest_real_data.py")

    if not obsoletas and not faltantes:
        print("\nEl esquema de la base coincide con los modelos.")


if __name__ == "__main__":
    main()
