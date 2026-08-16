"""
image_renderer.py
------------------
Convierte los desgloses deterministas de ChatResponse en infografías PNG,
para canales que no pueden renderizar HTML (WhatsApp Cloud API solo acepta
imágenes ya rasterizadas).

Decisión de diseño: se usa Pillow (dibujo directo) en vez de un navegador
headless (Playwright). Para un hackathon esto es más robusto porque:
  - No depende de descargar un binario de Chromium (~300 MB) en la máquina
    de despliegue, que puede fallar por red o tiempo durante la demo.
  - El proceso de render es ~10-20x más rápido y usa una fracción de la RAM,
    lo que importa en planes gratuitos de hosting con memoria acotada.
  - No hay que mantener una plantilla HTML/CSS en paralelo a la de la web.

El costo es que el layout se calcula a mano (coordenadas, wrapping de texto)
en vez de dejarlo a un motor de layout. Las funciones de dibujo de abajo
están aisladas justamente para acotar ese costo a un solo archivo.

Principio de una sola fuente de verdad (igual que en visuals.js para web):
este módulo NUNCA recalcula montos de negocio. Todos los números que dibuja
vienen tal cual del ChatResponse que ya construyó el motor determinista
(app/services/deterministic.py + orchestrator.py). Lo único que se calcula
aquí son sumas de presentación (total = suma de líneas ya verificadas) y
porcentajes de barra, nunca una variación o un evento de facturación nuevo.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.core.schemas import ChatResponse

# ---------------------------------------------------------------------------
# Configuración visual: replica las variables CSS de app/static/styles.css
# para que la infografía de WhatsApp se vea como la tarjeta web.
# ---------------------------------------------------------------------------

COLOR_PRIMARY = (79, 70, 229)        # --primary
COLOR_PRIMARY_DARK = (55, 48, 163)   # extremo del --primary-gradient
COLOR_PRIMARY_LIGHT = (238, 242, 255)  # --primary-light
COLOR_TEXT_MAIN = (15, 23, 42)       # --text-main
COLOR_TEXT_MUTED = (100, 116, 139)   # --text-muted
COLOR_BORDER = (226, 232, 240)       # --border-color
COLOR_SUCCESS = (5, 150, 105)        # variante oscura de --success (contraste)
COLOR_DANGER = (220, 38, 38)         # variante oscura de --danger
COLOR_WARNING = (180, 83, 9)         # variante oscura de --warning
COLOR_CARD_BG = (255, 255, 255)
COLOR_SECTION_BG = (248, 250, 252)   # #f8fafc
COLOR_BAR_TRACK = (241, 245, 249)    # #f1f5f9

# Lienzo cuadrado: cómodo tanto para el visor de imágenes de WhatsApp
# (recorta a un aspecto cercano a 1:1 en la miniatura del chat) como para
# compartir la infografía suelta fuera de la conversación.
CANVAS_WIDTH = 1080
MARGIN = 64

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _load_font(path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    """
    Carga una fuente variable (formato `[wght]`, un solo archivo con eje de
    peso) y la fija en el weight pedido. Si el archivo no está disponible
    (por ejemplo, si no se copió app/assets/fonts/ al desplegar), degrada a
    la fuente por defecto de Pillow en vez de romper el render completo.
    """
    try:
        font = ImageFont.truetype(str(path), size)
        try:
            font.set_variation_by_axes([weight])
        except Exception:
            pass  # fuente no variable o eje distinto: se usa el weight por defecto
        return font
    except Exception:
        return ImageFont.load_default()


def _fonts():
    outfit = FONTS_DIR / "Outfit.ttf"
    jakarta = FONTS_DIR / "PlusJakartaSans.ttf"
    return {
        "title": _load_font(outfit, 44, 700),
        "subtitle": _load_font(jakarta, 26, 500),
        "amount_label": _load_font(jakarta, 22, 700),
        "amount_big": _load_font(outfit, 52, 700),
        "amount_delta": _load_font(outfit, 40, 700),
        "section_label": _load_font(jakarta, 24, 700),
        "row_title": _load_font(jakarta, 28, 700),
        "row_sub": _load_font(jakarta, 22, 400),
        "row_amount": _load_font(jakarta, 28, 700),
        "footer": _load_font(jakarta, 20, 500),
        "chart_amount": _load_font(jakarta, 22, 700),
        "chart_month": _load_font(jakarta, 20, 500),
    }


# ---------------------------------------------------------------------------
# Utilidades de dibujo
# ---------------------------------------------------------------------------

def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    """Envuelve texto por palabra para que quepa en max_width px."""
    words = text.split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fmt_monto(monto: float) -> str:
    signo = "-" if monto < 0 else ""
    return f"{signo}S/ {abs(monto):,.2f}"


def _fmt_monto_con_signo(monto: float) -> str:
    signo = "+" if monto >= 0 else "-"
    return f"{signo}S/ {abs(monto):,.2f}"


MESES_CORTOS = {
    "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
    "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
    "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dic",
}


def _mes_corto(month_str: Optional[str]) -> str:
    if not month_str or "-" not in month_str:
        return month_str or ""
    partes = month_str.split("-")
    return MESES_CORTOS.get(partes[1], month_str) if len(partes) > 1 else month_str


def _draw_icon(draw: ImageDraw.ImageDraw, box, kind: str):
    """
    Dibuja un ícono vectorial simple con primitivas de Pillow (líneas y
    rectángulos), en vez de un glyph de emoji: las fuentes TTF normales no
    traen glyphs de emoji, así que un caracter como el de un documento se
    renderiza como un cuadro vacío ("tofu"). Con formas propias el ícono se
    ve igual sin importar el sistema operativo donde corra el backend.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lw = max(int(w * 0.07), 3)

    if kind == "document":
        pad_x, pad_y = w * 0.24, h * 0.16
        draw.rounded_rectangle(
            (x0 + pad_x, y0 + pad_y, x1 - pad_x, y1 - pad_y),
            radius=int(w * 0.06), outline=COLOR_PRIMARY, width=lw,
        )
        line_y_positions = [0.38, 0.55, 0.72]
        for frac in line_y_positions:
            ly = y0 + h * frac
            draw.line(
                (x0 + pad_x + w * 0.12, ly, x1 - pad_x - w * 0.12, ly),
                fill=COLOR_PRIMARY, width=max(int(lw * 0.6), 2),
            )
    elif kind == "bars":
        base_y = y1 - h * 0.2
        bar_w = w * 0.16
        heights = [0.30, 0.55, 0.42, 0.65]
        gap = (w - 2 * (w * 0.2) - len(heights) * bar_w) / (len(heights) - 1)
        bx = x0 + w * 0.2
        for frac in heights:
            bh = h * frac
            draw.rounded_rectangle(
                (bx, base_y - bh, bx + bar_w, base_y), radius=int(bar_w * 0.3), fill=COLOR_PRIMARY,
            )
            bx += bar_w + gap
    elif kind == "trend":
        pad = w * 0.2
        pts = [
            (x0 + pad, y1 - pad * 0.9),
            (x0 + w * 0.42, y0 + h * 0.55),
            (x0 + w * 0.6, y0 + h * 0.68),
            (x1 - pad, y0 + pad * 0.8),
        ]
        draw.line(pts, fill=COLOR_PRIMARY, width=lw, joint="curve")
        # Punta de flecha en el extremo superior derecho de la línea
        ax, ay = pts[-1]
        draw.line((ax, ay, ax - w * 0.14, ay), fill=COLOR_PRIMARY, width=lw)
        draw.line((ax, ay, ax, ay + h * 0.14), fill=COLOR_PRIMARY, width=lw)
        for px, py in pts:
            r = w * 0.035
            draw.ellipse((px - r, py - r, px + r, py + r), fill=COLOR_PRIMARY)


def _header(draw, f, y, icon_kind, title, subtitle):
    """Dibuja el encabezado común de toda tarjeta: ícono + título + subtítulo."""
    icon_size = 72
    icon_box = (MARGIN, y, MARGIN + icon_size, y + icon_size)
    _rounded_rect(draw, icon_box, radius=18, fill=COLOR_PRIMARY_LIGHT)
    _draw_icon(draw, icon_box, icon_kind)
    text_x = MARGIN + icon_size + 22
    draw.text((text_x, y + 4), title, font=f["title"], fill=COLOR_TEXT_MAIN)
    draw.text((text_x, y + 4 + 50), subtitle, font=f["subtitle"], fill=COLOR_TEXT_MUTED)
    return y + icon_size + 36


def _divider(draw, y, width):
    draw.line([(MARGIN, y), (MARGIN + width, y)], fill=COLOR_BORDER, width=2)
    return y + 30


def _footer_watermark(draw, f, y):
    texto = "Generado por Lucía · Copiloto de Facturación"
    tw = _text_width(draw, texto, f["footer"])
    draw.text(
        ((CANVAS_WIDTH - tw) / 2, y),
        texto, font=f["footer"], fill=COLOR_TEXT_MUTED,
    )


def _finalize(img: Image.Image, bottom_content_y: int, margin_bottom: int = 90) -> bytes:
    """Recorta el canvas a la altura realmente usada y devuelve bytes PNG."""
    final_height = bottom_content_y + margin_bottom
    img = img.crop((0, 0, CANVAS_WIDTH, final_height))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _new_canvas(height_estimate: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (CANVAS_WIDTH, height_estimate), COLOR_SECTION_BG)
    return img, ImageDraw.Draw(img)


# ---------------------------------------------------------------------------
# Visual 1 — "Así cambió tu recibo" (variation_breakdown)
# ---------------------------------------------------------------------------

def render_variation_card(chat_response: ChatResponse) -> bytes:
    f = _fonts()
    items = chat_response.variation_breakdown or []

    current_total = sum(i.monto for i in (chat_response.current_bill_breakdown or []))
    if chat_response.historical_bills_summary:
        previous_total = chat_response.historical_bills_summary[0].amount
    else:
        previous_total = current_total - sum(i.impacto for i in items)
    if not current_total and previous_total is not None:
        current_total = previous_total + sum(i.impacto for i in items)

    diferencia = round(current_total - previous_total, 2)
    subio = diferencia > 0.004
    bajo = diferencia < -0.004
    color_delta = COLOR_DANGER if subio else (COLOR_SUCCESS if bajo else COLOR_TEXT_MUTED)

    # Estimación generosa: una etiqueta y una lista de conceptos largas
    # pueden envolver a varias líneas, y el alto real solo se conoce al
    # dibujar. Es más seguro sobredimensionar el lienzo y recortarlo al
    # final (_finalize) que arriesgarse a que Pillow recorte texto en
    # silencio por quedarse corto de espacio.
    row_height_estimate = 230
    height = 480 + len(items) * row_height_estimate + 200
    img, draw = _new_canvas(height)

    _rounded_rect(draw, (24, 24, CANVAS_WIDTH - 24, height - 24), radius=32, fill=COLOR_CARD_BG)

    y = 56
    y = _header(draw, f, y, "document", "Así cambió tu recibo",
                "Comparación con el ciclo anterior")
    y = _divider(draw, y, CANVAS_WIDTH - 2 * MARGIN)

    # Comparación de montos: anterior -> actual
    col_w = (CANVAS_WIDTH - 2 * MARGIN - 140) / 2
    label_prev = "ANTERIOR"
    label_curr = "ACTUAL"
    amt_prev = _fmt_monto(previous_total)
    amt_curr = _fmt_monto(current_total)

    lw = _text_width(draw, label_prev, f["amount_label"])
    aw = _text_width(draw, amt_prev, f["amount_big"])
    cx1 = MARGIN + col_w / 2
    draw.text((cx1 - lw / 2, y), label_prev, font=f["amount_label"], fill=COLOR_TEXT_MUTED)
    draw.text((cx1 - aw / 2, y + 40), amt_prev, font=f["amount_big"], fill=COLOR_TEXT_MAIN)

    arrow_font = _load_font(FONTS_DIR / "Outfit.ttf", 44, 700)
    aw2 = _text_width(draw, "\u2192", arrow_font)
    draw.text((MARGIN + col_w + 70 - aw2 / 2, y + 40), "\u2192", font=arrow_font, fill=COLOR_TEXT_MUTED)

    lw2 = _text_width(draw, label_curr, f["amount_label"])
    aw3 = _text_width(draw, amt_curr, f["amount_big"])
    cx2 = MARGIN + col_w + 140 + col_w / 2
    draw.text((cx2 - lw2 / 2, y), label_curr, font=f["amount_label"], fill=COLOR_TEXT_MUTED)
    draw.text((cx2 - aw3 / 2, y + 40), amt_curr, font=f["amount_big"], fill=COLOR_PRIMARY)

    y += 130

    # Delta grande y centrado
    delta_txt = _fmt_monto_con_signo(diferencia)
    dw = _text_width(draw, delta_txt, f["amount_delta"])
    draw.text(((CANVAS_WIDTH - dw) / 2, y), delta_txt, font=f["amount_delta"], fill=color_delta)
    y += 80

    y = _divider(draw, y, CANVAS_WIDTH - 2 * MARGIN)

    # Lista causal
    if items:
        draw.text((MARGIN, y), "¿QUÉ PROVOCÓ EL CAMBIO?", font=f["section_label"], fill=COLOR_TEXT_MUTED)
        y += 46

        for item in items:
            row_top = y
            row_w = CANVAS_WIDTH - 2 * MARGIN
            impacto_txt = _fmt_monto_con_signo(item.impacto)
            impacto_color = COLOR_DANGER if item.impacto > 0.004 else (
                COLOR_SUCCESS if item.impacto < -0.004 else COLOR_TEXT_MUTED
            )
            impacto_w = _text_width(draw, impacto_txt, f["row_amount"])

            # La etiqueta se envuelve dejando siempre libre la columna del
            # monto en su primera línea, para que un texto largo nunca quede
            # dibujado debajo del importe (montos y etiquetas vienen del
            # backend con longitud variable, no se puede asumir que quepan).
            title_max_w = row_w - impacto_w - 24
            title_lines = _wrap_text(draw, item.etiqueta, f["row_title"], max(title_max_w, 120))
            draw.text((MARGIN, row_top), title_lines[0], font=f["row_title"], fill=COLOR_TEXT_MAIN)
            draw.text(
                (MARGIN + row_w - impacto_w, row_top),
                impacto_txt, font=f["row_amount"], fill=impacto_color,
            )
            sub_y = row_top + 42
            for extra_line in title_lines[1:]:
                draw.text((MARGIN, sub_y), extra_line, font=f["row_title"], fill=COLOR_TEXT_MAIN)
                sub_y += 40

            conceptos_txt = ", ".join(item.conceptos) if item.conceptos else ""
            if conceptos_txt:
                for line in _wrap_text(draw, conceptos_txt, f["row_sub"], row_w - impacto_w - 20)[:2]:
                    draw.text((MARGIN, sub_y), line, font=f["row_sub"], fill=COLOR_TEXT_MUTED)
                    sub_y += 30

            y = sub_y + 20
            draw.line([(MARGIN, y), (MARGIN + row_w, y)], fill=COLOR_BORDER, width=1)
            y += 24

    _footer_watermark(draw, f, y + 10)
    return _finalize(img, y + 10, margin_bottom=60)


# ---------------------------------------------------------------------------
# Visual 2 — "¿En qué se compone tu recibo?" (current_bill_breakdown)
# ---------------------------------------------------------------------------

def render_breakdown_card(chat_response: ChatResponse) -> bytes:
    f = _fonts()
    items = chat_response.current_bill_breakdown or []
    total = sum(i.monto for i in items)
    max_monto = max((i.monto for i in items), default=1) or 1

    row_height_estimate = 200
    height = 420 + len(items) * row_height_estimate + 200
    img, draw = _new_canvas(height)

    _rounded_rect(draw, (24, 24, CANVAS_WIDTH - 24, height - 24), radius=32, fill=COLOR_CARD_BG)

    y = 56
    y = _header(draw, f, y, "bars", "¿En qué se compone tu recibo?",
                "Desglose por categoría de cargo")
    y = _divider(draw, y, CANVAS_WIDTH - 2 * MARGIN)

    row_w = CANVAS_WIDTH - 2 * MARGIN
    track_h = 22

    for item in items:
        monto_txt = _fmt_monto(item.monto)
        monto_w = _text_width(draw, monto_txt, f["row_amount"])
        title_max_w = row_w - monto_w - 24
        title_lines = _wrap_text(draw, item.etiqueta, f["row_title"], max(title_max_w, 120))
        draw.text((MARGIN, y), title_lines[0], font=f["row_title"], fill=COLOR_TEXT_MAIN)
        draw.text((MARGIN + row_w - monto_w, y), monto_txt, font=f["row_amount"], fill=COLOR_TEXT_MAIN)
        y += 44
        for extra_line in title_lines[1:]:
            draw.text((MARGIN, y), extra_line, font=f["row_title"], fill=COLOR_TEXT_MAIN)
            y += 40

        # Barra proporcional al ítem de mayor monto
        _rounded_rect(draw, (MARGIN, y, MARGIN + row_w, y + track_h), radius=track_h // 2, fill=COLOR_BAR_TRACK)
        fill_w = max(int(row_w * (item.monto / max_monto)), track_h)
        _rounded_rect(draw, (MARGIN, y, MARGIN + fill_w, y + track_h), radius=track_h // 2, fill=COLOR_PRIMARY)
        y += track_h + 14

        conceptos_txt = ", ".join(item.conceptos) if item.conceptos else ""
        if conceptos_txt:
            for line in _wrap_text(draw, conceptos_txt, f["row_sub"], row_w)[:2]:
                draw.text((MARGIN, y), line, font=f["row_sub"], fill=COLOR_TEXT_MUTED)
                y += 30

        y += 22
        draw.line([(MARGIN, y), (MARGIN + row_w, y)], fill=COLOR_BORDER, width=1)
        y += 26

    # Total
    total_txt = _fmt_monto(total)
    total_w = _text_width(draw, total_txt, f["amount_delta"])
    draw.text((MARGIN, y), "TOTAL DEL RECIBO", font=f["section_label"], fill=COLOR_TEXT_MUTED)
    draw.text((MARGIN + row_w - total_w, y - 8), total_txt, font=f["amount_delta"], fill=COLOR_PRIMARY)
    y += 60

    _footer_watermark(draw, f, y)
    return _finalize(img, y, margin_bottom=60)


# ---------------------------------------------------------------------------
# Visual 3 — "Evolución de tu recibo" (historical_bills_summary + actual)
# ---------------------------------------------------------------------------

def render_history_card(chat_response: ChatResponse) -> bytes:
    f = _fonts()
    historial = list(reversed(chat_response.historical_bills_summary or []))  # antiguo -> reciente
    current_total = sum(i.monto for i in (chat_response.current_bill_breakdown or []))

    puntos = [(_mes_corto(b.month), b.amount, False) for b in historial]
    if current_total:
        puntos.append(("Actual", current_total, True))

    height = 620
    img, draw = _new_canvas(height)
    _rounded_rect(draw, (24, 24, CANVAS_WIDTH - 24, height - 24), radius=32, fill=COLOR_CARD_BG)

    y = 56
    y = _header(draw, f, y, "trend", "Evolución de tu recibo",
                "Últimos ciclos facturados")
    y = _divider(draw, y, CANVAS_WIDTH - 2 * MARGIN)

    chart_top = y + 20
    chart_bottom = height - 150
    chart_h = chart_bottom - chart_top
    chart_w = CANVAS_WIDTH - 2 * MARGIN

    if puntos:
        max_amt = max(p[1] for p in puntos) or 1
        n = len(puntos)
        gap = 24
        bar_w = (chart_w - gap * (n - 1)) / n

        for idx, (label, amount, es_actual) in enumerate(puntos):
            bx0 = MARGIN + idx * (bar_w + gap)
            bx1 = bx0 + bar_w
            bar_h = max(int(chart_h * (amount / max_amt)), 10)
            by0 = chart_bottom - bar_h
            color = COLOR_PRIMARY if es_actual else (147, 197, 253) if False else (165, 180, 252)
            if es_actual:
                color = COLOR_PRIMARY
            _rounded_rect(draw, (bx0, by0, bx1, chart_bottom), radius=10, fill=color)

            amt_txt = f"S/{amount:,.0f}"
            aw = _text_width(draw, amt_txt, f["chart_amount"])
            draw.text((bx0 + bar_w / 2 - aw / 2, by0 - 32), amt_txt, font=f["chart_amount"], fill=COLOR_TEXT_MAIN)

            mw = _text_width(draw, label, f["chart_month"])
            draw.text(
                (bx0 + bar_w / 2 - mw / 2, chart_bottom + 14), label,
                font=f["chart_month"],
                fill=COLOR_PRIMARY if es_actual else COLOR_TEXT_MUTED,
            )

    y = chart_bottom + 60
    _footer_watermark(draw, f, y)
    return _finalize(img, y, margin_bottom=50)


# ---------------------------------------------------------------------------
# Selección de un único visual por turno (evita saturar el chat de WhatsApp)
# ---------------------------------------------------------------------------

def select_and_render_visual(chat_response: ChatResponse) -> Optional[bytes]:
    """
    Elige, entre los tres visuales, el más relevante según qué datos
    verificados trajo este turno concreto, y lo renderiza a PNG.

    Prioridad: variación (explica un cambio) > desglose (qué compone el
    recibo) > histórico (evolución). Se envía como máximo UNA imagen por
    turno para que WhatsApp siga sintiéndose como una conversación y no
    como un dashboard.
    """
    if chat_response.requires_human_intervention:
        return None
    if chat_response.variation_breakdown:
        return render_variation_card(chat_response)
    if chat_response.current_bill_breakdown:
        return render_breakdown_card(chat_response)
    if chat_response.historical_bills_summary and len(chat_response.historical_bills_summary) > 1:
        return render_history_card(chat_response)
    return None
