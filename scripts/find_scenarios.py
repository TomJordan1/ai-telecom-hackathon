"""
find_scenarios.py
-----------------
Recorre FACTURACION_CLIENTES.csv y clasifica cada cuenta financiera según los
escenarios críticos que el desafío pide demostrar en vivo:

    (a) Prorrateos
    (b) Cuota de equipo financiado
    (c) Cobro por reconexión tras suspensión morosa
    (d) Fin de descuentos
    (e) Cambios de plan

Reutiliza EXACTAMENTE el mismo motor de clasificación que usa la aplicación
(`app.services.deterministic`), así que lo que reporta aquí es lo que Lucía
detectará en tiempo de ejecución: no es una heurística paralela.

Uso:
    python scripts/find_scenarios.py                  # informe de cobertura
    python scripts/find_scenarios.py --json ruta.json # guarda las cuentas elegidas
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import deterministic as det  # noqa: E402

CSV_FACTURACION = PROJECT_ROOT / "disclaimer" / "FACTURACION_CLIENTES.csv"

# Cuántos ciclos necesita una cuenta para que la comparación tenga sentido.
MIN_CICLOS = 3

# Evento detectado -> escenario del desafío.
ESCENARIOS = {
    "PRORRATEO_CAMBIO_PLAN": "a_prorrateo",
    "CUOTA_EQUIPO": "b_cuota_equipo",
    "RECONEXION_MOROSIDAD": "c_reconexion",
    "FIN_PROMOCION": "d_fin_descuento",
    "CAMBIO_PLAN": "e_cambio_plan",
}


class ReciboSimple:
    """Réplica mínima del recibo virtual que expone crud.VirtualRecibo."""

    def __init__(self, ciclo, cargos):
        self.ciclo = ciclo
        self.mes_emision = f"{ciclo[:4]}-{ciclo[4:6]}" if len(ciclo) >= 6 else ciclo
        self.monto_total = round(sum(c["CHARGE_TOTAL_AMOUNT"] for c in cargos), 2)
        self.conceptos_facturados = {"cargos": cargos, "info_factura": {}}


def cargar_cuentas():
    """Agrupa los cargos del CSV por cuenta y ciclo."""
    cuentas = defaultdict(lambda: defaultdict(list))
    with open(CSV_FACTURACION, encoding="utf-8", newline="") as f:
        lector = csv.DictReader(f, delimiter=";")
        for fila in lector:
            cuenta = (fila.get("FINANCIAL_ACCOUNT_KEY") or "").strip()
            ciclo = (fila.get("ciclo") or "").strip()
            if not cuenta or not ciclo:
                continue
            try:
                total = float(fila.get("CHARGE_TOTAL_AMOUNT") or 0.0)
            except ValueError:
                total = 0.0
            cuentas[cuenta][ciclo].append({
                "CHARGE_CODE_ID": (fila.get("CHARGE_CODE_ID") or "").strip(),
                "CHARGE_CODE_DESC": (fila.get("CHARGE_CODE_DESC") or "").strip(),
                "CHARGE_CODE_CLASSIFICATION": (fila.get("CHARGE_CODE_CLASSIFICATION") or "").strip(),
                "CHARGE_TOTAL_AMOUNT": total,
                "GRUPO": (fila.get("GRUPO") or "").strip(),
                "SUB_GRUPO": (fila.get("SUB_GRUPO") or "").strip(),
            })
    return cuentas


def evaluar(cuentas):
    """Determina, cuenta por cuenta, qué evento detectaría el motor real."""
    por_escenario = defaultdict(list)
    resumen_eventos = defaultdict(int)

    for cuenta, ciclos in cuentas.items():
        if len(ciclos) < MIN_CICLOS:
            continue

        recibos = [
            ReciboSimple(ciclo, cargos)
            for ciclo, cargos in sorted(ciclos.items(), reverse=True)
        ]
        actual, previo = recibos[0], recibos[1]
        delta = round(actual.monto_total - previo.monto_total, 2)

        componentes = det.descomponer_variacion(
            actual.conceptos_facturados["cargos"],
            previo.conceptos_facturados["cargos"],
        )
        evento = det._detectar_evento(componentes, delta)
        resumen_eventos[evento] += 1

        escenario = ESCENARIOS.get(evento)
        if escenario:
            por_escenario[escenario].append({
                "cuenta": cuenta,
                "evento": evento,
                "ciclos": len(ciclos),
                "monto_actual": actual.monto_total,
                "monto_anterior": previo.monto_total,
                "variacion": delta,
            })

    return por_escenario, resumen_eventos


def main():
    parser = argparse.ArgumentParser(description="Localiza cuentas por escenario del desafío.")
    parser.add_argument("--json", help="Ruta donde guardar las cuentas seleccionadas.")
    parser.add_argument("--por-escenario", type=int, default=5,
                        help="Cuántas cuentas listar/guardar por escenario.")
    args = parser.parse_args()

    print(f"Leyendo {CSV_FACTURACION.name}...")
    cuentas = cargar_cuentas()
    print(f"Cuentas con cargos: {len(cuentas)}")

    por_escenario, resumen_eventos = evaluar(cuentas)

    print(f"\nCuentas con al menos {MIN_CICLOS} ciclos, por evento detectado:")
    for evento, total in sorted(resumen_eventos.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {evento:28} {total}")

    print("\nCobertura de los escenarios críticos del desafío:")
    seleccion = {}
    for escenario in sorted(ESCENARIOS.values()):
        candidatos = por_escenario.get(escenario, [])
        # Se priorizan las cuentas con más ciclos y mayor variación absoluta:
        # son las que mejor evidencian la explicación en una demo.
        candidatos.sort(key=lambda c: (c["ciclos"], abs(c["variacion"])), reverse=True)
        elegidos = candidatos[:args.por_escenario]
        seleccion[escenario] = elegidos
        estado = "OK" if elegidos else "SIN COBERTURA"
        print(f"\n  [{estado}] {escenario}  (candidatos: {len(candidatos)})")
        for c in elegidos:
            print(f"     cuenta {c['cuenta']:>12}  ciclos={c['ciclos']}  "
                  f"{c['monto_anterior']:>9.2f} -> {c['monto_actual']:>9.2f}  "
                  f"variacion={c['variacion']:>+9.2f}")

    if args.json:
        destino = Path(args.json)
        destino.write_text(json.dumps(seleccion, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSelección guardada en {destino}")


if __name__ == "__main__":
    main()
