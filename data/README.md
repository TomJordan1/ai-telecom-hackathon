# data/

## raw_movistar/
Dataset real entregado por el equipo de datos. Contiene:
- `REGISTROS_CLIENTES_20MIL.csv` — padrón de clientes
- `Cargos_FacturadosV2.csv` — detalle de cargos facturados
- `Ordenes.csv` — historial de gestiones (suspensiones, reconexiones, cambios)
- `Diccionario de datos.xlsx` — definición de columnas

Lo consume `scripts/generate_mock_data.py` para poblar `lucia_brain.db`.

**No subir esta carpeta a un repositorio público** — contiene información
real de clientes (cuentas financieras, deuda, historial de servicio). Ya
está en `.gitignore`.

## usuarios_demo.txt
Se genera automáticamente cada vez que corres `scripts/generate_mock_data.py`.
Lista los `user_id` reales elegidos para representar cada escenario de la
demo (fin de promo, prorrateo, cuota de equipo, reconexión, deuda activa).
Úsalo como referencia si necesitas actualizar `app/static/index.html`,
`app/api/whatsapp.py` o `scripts/telegram_bot.py` después de recargar con
una muestra distinta.
