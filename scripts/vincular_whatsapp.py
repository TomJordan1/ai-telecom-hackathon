"""
vincular_whatsapp.py
--------------------
Utilidad CLI para vincular un número de WhatsApp a una cuenta financiera
en la base de datos local o consultar cuentas con alertas de vencimiento activas.

Uso:
    # 1. Listar cuentas con alertas de fin de promoción
    python scripts/vincular_whatsapp.py --listar-alertas

    # 2. Vincular un número a una cuenta específica
    python scripts/vincular_whatsapp.py --numero 51987654321 --cuenta 102968745

    # 3. Vincular automáticamente a la primera cuenta con alertas activa
    python scripts/vincular_whatsapp.py --numero 51987654321 --auto-alerta
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


from app.db.database import SessionLocal
from app.db import crud
from app.services.deterministic import calculate_billing_facts
from app.services.whatsapp_sender import send_whatsapp_text


def main():
    parser = argparse.ArgumentParser(description="Gestión de contactos de WhatsApp para testing.")
    parser.add_argument("--numero", help="Número de WhatsApp con código de país (ej. 51987654321)")
    parser.add_argument("--cuenta", help="Cuenta financiera a vincular (ej. 102968745)")
    parser.add_argument("--listar-alertas", action="store_true", help="Lista cuentas con alertas activas.")
    parser.add_argument("--listar-contactos", action="store_true", help="Lista contactos vinculados.")
    parser.add_argument("--auto-alerta", action="store_true", help="Vincula el número a la primera cuenta con alerta.")
    parser.add_argument("--enviar-alerta", action="store_true", help="Envía alerta de prueba al número vinculado.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.listar_contactos:
            contactos = crud.get_all_contactos(db)
            con_wa = [c for c in contactos if c.whatsapp_number][:25]
            print(f"\n--- Contactos con WhatsApp Vinculado (mostrando {len(con_wa)} de {len(contactos)} registros) ---")
            for c in con_wa:
                num = str(c.whatsapp_number or "Sin número")
                print(f"  WA: +{num:<16} -> Cuenta: {c.user_id}")
            return


        if args.listar_alertas:
            user_ids = crud.get_all_user_ids(db)[:50]
            print(f"\nBuscando cuentas con alertas de fin de promoción...")
            encontradas = 0
            for uid in user_ids:
                facts = calculate_billing_facts(uid, db)
                alertas = facts.get("upcoming_alerts") or []
                if alertas:
                    encontradas += 1
                    a = alertas[0]
                    print(f"  Cuenta: {uid:>12} | Plan: {facts.get('plan_actual', '—')} | "
                          f"Promo: {a.get('concepto')} | Impacto: {a.get('impacto_estimado')}")
                    if encontradas >= 10:
                        break
            print(f"Total mostradas: {encontradas}")
            return


        if args.auto_alerta and args.numero:
            user_ids = crud.get_all_user_ids(db)[:200]
            elegida = None
            for uid in user_ids:
                facts = calculate_billing_facts(uid, db)
                if facts.get("upcoming_alerts"):
                    elegida = uid
                    break

            if not elegida:
                print("No se encontró ninguna cuenta con alertas activas en la muestra.")
                return

            contacto = crud.upsert_contacto_usuario(db, user_id=elegida, whatsapp_number=args.numero)
            print(f"✅ Número +{args.numero} vinculado a cuenta con alertas: {elegida}")
            if args.enviar_alerta:
                facts = calculate_billing_facts(elegida, db)
                alerta = facts["upcoming_alerts"][0]
                from app.services.llm import generate_proactive_alert_message
                historial = crud.get_historial_reciente_usuario(db, user_id=elegida, whatsapp_number=args.numero)
                msg = generate_proactive_alert_message(alerta, historial_conversacion=historial)
                send_whatsapp_text(args.numero, msg)
                print(f"🚀 Alerta enviada a +{args.numero}:\n{msg}")
            return

        if args.numero and args.cuenta:
            if not crud.verificar_existe_cuenta(db, args.cuenta):
                print(f"❌ Error: La cuenta '{args.cuenta}' no existe en facturación.")
                sys.exit(1)

            contacto = crud.upsert_contacto_usuario(db, user_id=args.cuenta, whatsapp_number=args.numero)
            print(f"✅ Número +{args.numero} vinculado exitosamente a la cuenta {args.cuenta}.")
            if args.enviar_alerta:
                facts = calculate_billing_facts(args.cuenta, db)
                alertas = facts.get("upcoming_alerts") or []
                if alertas:
                    from app.services.llm import generate_proactive_alert_message
                    historial = crud.get_historial_reciente_usuario(db, user_id=args.cuenta, whatsapp_number=args.numero)
                    msg = generate_proactive_alert_message(alertas[0], historial_conversacion=historial)
                else:
                    msg = f"🔔 Prueba de conexión: tu cuenta {args.cuenta} está vinculada correctamente a Lucía."
                send_whatsapp_text(args.numero, msg)
                print(f"🚀 Mensaje enviado a +{args.numero}:\n{msg}")
            return


        parser.print_help()
    finally:
        db.close()


if __name__ == "__main__":
    main()
