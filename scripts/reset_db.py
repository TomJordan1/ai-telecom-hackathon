"""
reset_db.py
-----------
Reestablece el esquema de la base operacional (Supabase Postgres o SQLite).

Existe porque `Base.metadata.create_all` solo crea tablas nuevas: nunca elimina
las que dejaron de estar definidas. Al migrar del set de datos ficticio al
dataset real quedaron tablas huérfanas (`recibos_cliente`, `catalogo_planes`)
que ya ningún modelo describe.

OPERACIONES (todas explícitas, ninguna por defecto):

    --drop-obsoletas   Elimina solo las tablas que ya no define ningún modelo.
                       No toca `documentos_politicas` (tabla vectorial del RAG,
                       gestionada por scripts/setup_supabase.sql).

    --drop-operacional Vacía las tablas de estado conversacional: historial,
                       memoria emocional, cuarentena, base de casos, auditoría e
                       idempotencia. Útil para empezar una demo desde cero.
                       NO borra los datos de facturación.

    --drop-todo        Elimina TODAS las tablas que definen los modelos. Después
                       hay que volver a ejecutar scripts/ingest_real_data.py.

    --fix-constraints  Elimina claves ajenas que la base tiene de más respecto a
                       los modelos y que rompen POST /chat cuando un cliente
                       vuelve con un session_id nuevo. No borra ninguna fila.

Siempre pide confirmación escrita salvo que se pase --yes.

    python scripts/reset_db.py --drop-obsoletas
    python scripts/reset_db.py --drop-operacional
    python scripts/reset_db.py --drop-todo --yes
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, text  # noqa: E402

from app.db.database import Base, engine  # noqa: E402
from app.db import models  # noqa: F401,E402  (registra los modelos en Base.metadata)

# La tabla vectorial del RAG no la define ningún modelo de SQLAlchemy a
# propósito: vive en Supabase con su propia extensión (pgvector) y su función
# RPC. Nunca debe eliminarse desde aquí, o el RAG deja de responder.
TABLAS_PROTEGIDAS = {"documentos_politicas"}

# Estado conversacional y de aprendizaje. Se puede vaciar sin perder el dataset.
#
# EL ORDEN IMPORTA: en Supabase existe una clave ajena de audit_log.session_id
# hacia historial_interacciones.session_id, así que la auditoría debe vaciarse
# antes que el historial o el DELETE falla por violación de integridad.
TABLAS_OPERACIONALES = [
    "audit_log",
    "cuarentena_casos",
    "base_casos",
    "historial_interacciones",
    "mensajes_procesados",
]


# Claves ajenas que existen en la base pero NO en los modelos de SQLAlchemy.
#
# Aparecieron apuntando session_id de audit_log y cuarentena_casos hacia
# historial_interacciones. Contradicen el diseño en dos puntos:
#
#   1. `get_or_create_historial` reasigna el session_id del historial cuando un
#      cliente vuelve con una sesión nueva (memoria de largo plazo por cliente).
#      Con la clave ajena, ese UPDATE falla porque la auditoría ya referencia el
#      session_id anterior.
#   2. audit_log y cuarentena_casos son bitácoras de solo-añadir: deben poder
#      registrar un turno aunque su sesión ya no exista, no restringirla.
#
# El síntoma era un 500 en POST /chat para cualquier cliente que volviera con un
# session_id distinto al de su último turno.
FKS_INDEBIDAS = [
    ("audit_log", "audit_log_session_id_fkey"),
    ("cuarentena_casos", "cuarentena_casos_session_id_fkey"),
]


def fix_constraints(auto: bool):
    """Elimina las claves ajenas que la base tiene de más respecto a los modelos."""
    if engine.dialect.name != "postgresql":
        print("Solo aplica a PostgreSQL; en SQLite no existen estas restricciones.")
        return

    sql_buscar = text(
        """
        SELECT cl.relname, con.conname
        FROM pg_constraint con
        JOIN pg_class cl  ON cl.oid = con.conrelid
        JOIN pg_class ref ON ref.oid = con.confrelid
        WHERE con.contype = 'f' AND ref.relname = 'historial_interacciones'
        """
    )
    with engine.connect() as conn:
        presentes = {(f[0], f[1]) for f in conn.execute(sql_buscar)}

    objetivo = [(t, c) for t, c in FKS_INDEBIDAS if (t, c) in presentes]
    if not objetivo:
        print("No hay claves ajenas indebidas sobre historial_interacciones.")
        return

    print("Claves ajenas a eliminar (no están declaradas en los modelos y rompen "
          "la reasignación de session_id):")
    for tabla, nombre in objetivo:
        print(f"  - {tabla}.{nombre}")
    print("Solo se elimina la restricción; no se borra ninguna fila.")

    if not _confirmar("Se van a ELIMINAR esas restricciones de integridad.", auto):
        print("Cancelado: no se modificó ninguna restricción.")
        return

    _ejecutar([
        f'ALTER TABLE "{tabla}" DROP CONSTRAINT IF EXISTS "{nombre}"'
        for tabla, nombre in objetivo
    ])


def _confirmar(mensaje: str, auto: bool) -> bool:
    if auto:
        return True
    print(f"\n{mensaje}")
    respuesta = input("Escribe 'CONFIRMO' para continuar: ").strip()
    return respuesta == "CONFIRMO"


def _ejecutar(sentencias):
    """Ejecuta cada sentencia en su propia transacción y reporta el resultado."""
    for sql in sentencias:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
            print(f"  OK   {sql}")
        except Exception as e:
            print(f"  FALLO {sql}\n        {e}")


def drop_obsoletas(auto: bool):
    inspector = inspect(engine)
    existentes = set(inspector.get_table_names())
    definidas = set(Base.metadata.tables.keys())
    obsoletas = sorted(existentes - definidas - TABLAS_PROTEGIDAS)

    if not obsoletas:
        print("No hay tablas obsoletas: el esquema coincide con los modelos.")
        return

    print("Tablas obsoletas detectadas (ya no las define ningún modelo):")
    for tabla in obsoletas:
        print(f"  - {tabla}")

    if not _confirmar(f"Se van a ELIMINAR {len(obsoletas)} tabla(s). Esta acción no se puede deshacer.", auto):
        print("Cancelado: no se eliminó ninguna tabla.")
        return

    _ejecutar([f'DROP TABLE IF EXISTS "{t}" CASCADE' for t in obsoletas])


def drop_operacional(auto: bool):
    inspector = inspect(engine)
    existentes = set(inspector.get_table_names())
    objetivo = [t for t in TABLAS_OPERACIONALES if t in existentes]

    if not objetivo:
        print("No hay tablas de estado conversacional que vaciar.")
        return

    print("Se vaciará el estado conversacional y de aprendizaje:")
    for tabla in objetivo:
        print(f"  - {tabla}")
    print("Los datos de facturación (facturacion_clientes, planta_clientes, "
          "catalogo_ofertas, ordenes_cliente, notas_credito) NO se tocan.")

    if not _confirmar("Se van a BORRAR todas las filas de esas tablas.", auto):
        print("Cancelado: no se borró ninguna fila.")
        return

    _ejecutar([f'DELETE FROM "{t}"' for t in objetivo])


def drop_todo(auto: bool):
    definidas = sorted(Base.metadata.tables.keys())
    print("Se eliminarán TODAS las tablas definidas por los modelos:")
    for tabla in definidas:
        print(f"  - {tabla}")
    print(f"Protegidas (no se tocan): {', '.join(sorted(TABLAS_PROTEGIDAS))}")
    print("\nDespués habrá que volver a ejecutar: python scripts/ingest_real_data.py")

    if not _confirmar(
        f"Se van a ELIMINAR {len(definidas)} tabla(s) con todos sus datos. "
        "Esta acción no se puede deshacer.",
        auto,
    ):
        print("Cancelado: no se eliminó ninguna tabla.")
        return

    _ejecutar([f'DROP TABLE IF EXISTS "{t}" CASCADE' for t in definidas])
    print("\nRecreando el esquema vacío a partir de los modelos...")
    Base.metadata.create_all(bind=engine)
    print("Esquema recreado.")


def main():
    parser = argparse.ArgumentParser(description="Reestablece el esquema de la base operacional.")
    parser.add_argument("--drop-obsoletas", action="store_true",
                        help="Elimina las tablas que ya no define ningún modelo.")
    parser.add_argument("--drop-operacional", action="store_true",
                        help="Vacía historial, cuarentena, base de casos, auditoría e idempotencia.")
    parser.add_argument("--drop-todo", action="store_true",
                        help="Elimina todas las tablas de los modelos y recrea el esquema vacío.")
    parser.add_argument("--fix-constraints", action="store_true",
                        help="Elimina las claves ajenas que la base tiene de más y rompen /chat.")
    parser.add_argument("--yes", action="store_true",
                        help="Omite la confirmación interactiva.")
    args = parser.parse_args()

    if not (args.drop_obsoletas or args.drop_operacional or args.drop_todo or args.fix_constraints):
        parser.print_help()
        return

    print("=" * 70)
    print(f"  reset_db  ->  motor: {engine.dialect.name}")
    print("=" * 70)

    if args.fix_constraints:
        fix_constraints(args.yes)
    if args.drop_obsoletas:
        drop_obsoletas(args.yes)
    if args.drop_operacional:
        drop_operacional(args.yes)
    if args.drop_todo:
        drop_todo(args.yes)


if __name__ == "__main__":
    main()
