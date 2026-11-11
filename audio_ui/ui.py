"""
Piezas compartidas por todas las pantallas: formatos, estilo (CSS), la hoja del
folleto corregida, el panel de coste y el móvil con la nota de voz.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

import voice
from data_source import Alert, Snapshot


# --------------------------------------------------------------------------
# Contexto: lo que todas las pantallas necesitan saber
# --------------------------------------------------------------------------

@dataclass
class Ctx:
    snap: Snapshot
    loaded_at: str
    api_key: str
    model_id: str
    cuenta: dict                       # store.load(): gestor + particular
    default_voices: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Formatos
# --------------------------------------------------------------------------

def eur(x: float, decimals: Optional[int] = None) -> str:
    """Formato español: 1.250.000 € / 157,50 €."""
    if decimals is None:
        decimals = 0 if float(x).is_integer() else 2
    s = f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def num(x: float, decimals: int = 2) -> str:
    """0.45 -> '0,45' (sin símbolo)."""
    return f"{x:.{decimals}f}".replace(".", ",")


def esc(s) -> str:
    return html.escape(str(s))


def show(markup: str) -> None:
    """Pinta HTML propio. Quita las líneas vacías: en Markdown una línea vacía
    corta el bloque HTML y el resto se mostraría como texto o código."""
    st.markdown("\n".join(line for line in markup.splitlines() if line.strip()),
                unsafe_allow_html=True)


def initials(name: str) -> str:
    parts = [p for p in str(name).split() if p]
    return ("".join(p[0] for p in parts[:2]) or "?").upper()


def first_name(name: str) -> str:
    return name.split()[0] if name.split() else name


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower() or "x"


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def when(raw: str) -> str:
    """'2026-11-10 18:42' -> 'hoy, 18:42' / 'ayer, 18:42' / '8 nov, 18:42'."""
    try:
        d = datetime.fromisoformat(str(raw).strip().replace("Z", ""))
    except ValueError:
        return str(raw)
    today = datetime.now().date()
    hour = f", {d:%H:%M}" if (d.hour or d.minute) else ""
    if d.date() == today:
        return f"hoy{hour}"
    if d.date() == today - timedelta(days=1):
        return f"ayer{hour}"
    return f"{d.day} {MESES[d.month - 1]}{hour}"


NUMBERS = {1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco",
           6: "seis", 7: "siete", 8: "ocho", 9: "nueve", 10: "diez"}


def in_words(n: int) -> str:
    return NUMBERS.get(n, str(n))


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def fund_short(fund_name: str) -> str:
    """'Fondo Global Equilibrado, clase A' -> 'Fondo Global Equilibrado'."""
    return fund_name.split(",")[0].strip()


def del_(fund: str) -> str:
    """'del Fondo X' / 'de Amundi X' — concordancia del titular."""
    return f"del {fund}" if fund.lower().startswith("fondo") else f"de {fund}"


# --------------------------------------------------------------------------
# Lenguaje de las alertas
# --------------------------------------------------------------------------

FEE_EVENTS = ("subida_comision", "fees")
DIVIDEND_EVENTS = ("recorte_dividendo", "dividends")

SECTION_BY_EVENT = {
    "subida_comision": "Costes y gastos corrientes",
    "fees": "Costes y gastos corrientes",
    "recorte_dividendo": "Política de distribución",
    "dividends": "Política de distribución",
    "strategy": "Política de inversión",
    "debt": "Situación financiera",
    "regulation": "Información regulatoria",
    "guidance": "Previsiones de la gestora",
}

VOICE_LABEL = {"principiante": "Voz cercana, ritmo pausado",
               "profesional": "Voz sobria, tono profesional"}


def headline(alert: Alert) -> str:
    fund = fund_short(alert.fund_name)
    if alert.event_type in FEE_EVENTS:
        verb = "subido" if alert.delta_pp >= 0 else "bajado"
        return f"La gestora ha {verb} la comisión {del_(fund)}"
    if alert.event_type in DIVIDEND_EVENTS:
        return f"El {fund} recorta su dividendo" if fund.lower().startswith("fondo") \
            else f"{fund} recorta su dividendo"
    return f"{alert.title}: {fund}"


def impact_eur(alert: Alert, amount: float) -> float:
    """Cuánto le cuesta al año a quien tiene `amount` €. Positivo = le perjudica.
    Cálculo determinista en Python: la IA nunca pone las cifras."""
    if alert.event_type in FEE_EVENTS:
        return round(amount * alert.delta_pp / 100, 2)
    if alert.event_type in DIVIDEND_EVENTS:
        return round(amount * -alert.delta_pp / 100, 2)
    return round(amount * abs(alert.delta_pp) / 100, 2)


def change_word(alert: Alert) -> str:
    return "subida" if alert.delta_pp >= 0 else "bajada"


def _num_pattern(v: float) -> str:
    """Patrón que encuentra 1,20% / 1,2 % / 1.20% dentro de la cita."""
    full = f"{v:.2f}"
    short = full.rstrip("0").rstrip(".")
    variants = {full, short, full.replace(".", ","), short.replace(".", ",")}
    alts = "|".join(re.escape(x) for x in sorted(variants, key=len, reverse=True))
    return rf"(?<![\d,.])(?:{alts})\s?%"


def marked_quote(alert: Alert) -> str:
    """La cita del folleto con el valor antiguo tachado y el nuevo en rojo,
    como un abogado corrigiendo un contrato. Si no encuentra las cifras en la
    cita, la muestra limpia y añade la corrección debajo."""
    q = esc(alert.evidence.quote)
    delta = f"{'+' if alert.delta_pp >= 0 else '−'}{num(abs(alert.delta_pp))} puntos"
    m_old = re.search(_num_pattern(alert.old_value), q)
    m_new = re.search(_num_pattern(alert.new_value), q[m_old.end():]) if m_old else None
    if m_old and m_new:
        ns, ne = m_old.end() + m_new.start(), m_old.end() + m_new.end()
        start = m_old.start()
        pre = re.search(r"\b(?:del|de)\s+$", q[:start])
        if pre:
            start = pre.start()
            # Espacio que no se parte: "del 1,20%" nunca queda separado en dos líneas.
            q = q[:pre.end() - 1] + " " + q[pre.end():]
        end = ne
        post = re.match(r"\s+anual(?:es)?", q[end:])
        if post:
            end += post.end()
        return (f'{q[:start]}<span class="hl">{q[start:m_old.start()]}'
                f'<span class="old">{q[m_old.start():m_old.end()]}</span>{q[m_old.end():ns]}'
                f'<span class="new" data-delta="{delta}">{q[ns:ne]}</span>{q[ne:end]}</span>{q[end:]}')
    return (f'{q}<span class="fix">Antes <span class="old">{num(alert.old_value)} %</span> · ahora '
            f'<span class="new" data-delta="{delta}">{num(alert.new_value)} %</span></span>')


# --------------------------------------------------------------------------
# Iconos (SVG en línea)
# --------------------------------------------------------------------------

ICON = {
    "mark": '<svg viewBox="0 0 24 24" fill="none"><path d="M12 2.5 4 5.6v5.9c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V5.6L12 2.5Z" fill="#15241F"/><path d="M8.2 12.2h1.6M11 9.6v5.2M13.4 8.2v8M15.8 10.6v3.2" stroke="#EEF1EC" stroke-width="1.7" stroke-linecap="round"/></svg>',
    "check": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="m5 12 5 5 9-10"/></svg>',
    "briefcase": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7M3 12.5h18M11 12.5v2h2v-2"/></svg>',
    "person": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="8" r="4"/><path d="M4 21c.9-4.2 4-6.5 8-6.5s7.1 2.3 8 6.5"/></svg>',
}


# --------------------------------------------------------------------------
# Estilo
# --------------------------------------------------------------------------

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Charis+SIL:ital,wght@0,400;0,700;1,400&display=swap');
:root{
  --paper:#EEF1EC; --sheet:#FFFFFF; --sheet-2:#F6F8F4;
  --ink:#15241F; --ink-2:#4B5B55; --ink-3:#7D8A85; --line:#D5DCD5;
  --redline:#C23B2E; --redline-soft:#F7E3DF; --ledger:#2E6B58; --ledger-soft:#E1EEE8; --marker:#F5DC6A;
  --ui:"Inter", system-ui, sans-serif;
  --doc:"Charis SIL", "Bitstream Charter", Charter, Georgia, serif;
}
/* ---------- Fuera lo que delata a Streamlit ---------- */
#MainMenu, footer, header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stStatusWidget"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="stAppDeployButton"] {display:none !important;}
html, body, .stApp {background:var(--paper) !important;}
.stApp, .stApp button, .stApp input, .stApp textarea, .stApp label, .stApp p {font-family:var(--ui);}
.stApp {color:var(--ink); font-feature-settings:"tnum" 1, "cv11" 1; -webkit-font-smoothing:antialiased;}
.pvg-with-rail [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"]:has(.st-key-rail) {padding-left:76px;}
[data-testid="stElementContainer"]:has(.pvg-css){margin-bottom:-1rem}
.block-container, [data-testid="stMainBlockContainer"] {padding:26px 44px 40px !important; max-width:1440px;}
/* ---------- Rail de navegación (botones de Streamlit pegados a la izquierda) ---------- */
.st-key-rail{position:fixed !important;left:0;top:0;bottom:0;width:76px !important;background:var(--ink);z-index:999990;
  padding:22px 0 18px;align-items:center !important;gap:10px !important;flex-wrap:nowrap !important}
.st-key-rail > div{width:auto !important;flex:none !important}
.st-key-rail .mark{width:40px;height:40px;border-radius:12px;background:var(--paper);display:grid;place-items:center;margin-bottom:22px}
.st-key-rail .mark svg{width:24px;height:24px}
.st-key-rail button{position:relative;width:44px !important;height:44px;min-height:44px;padding:0 !important;border-radius:12px !important;border:0 !important;
  background:transparent !important;color:#9FB0A9 !important;justify-content:center;box-shadow:none !important;transition:background .15s,color .15s}
.st-key-rail button [data-testid="stMarkdownContainer"]{display:none}
.st-key-rail button span{margin:0 !important}
.st-key-rail button [data-testid="stIconMaterial"]{font-size:22px}
.st-key-rail button:hover{background:#1E3029 !important;color:#fff !important}
.st-key-rail [data-testid="stBaseButton-primary"]{background:#24382F !important;color:#fff !important}
.st-key-rail .st-key-nav_ajustes{margin-top:auto}
/* ---------- Barra superior ---------- */
.pvg-brand{font-weight:600;font-size:17px;letter-spacing:-.2px;color:var(--ink);line-height:1.35}
.pvg-brand small{display:block;font-weight:400;font-size:13px;color:var(--ink-3);letter-spacing:0}
.pvg-status{display:flex;align-items:center;justify-content:flex-end;gap:22px;font-size:13.5px;color:var(--ink-2);flex-wrap:wrap}
.pvg-status .live{display:flex;align-items:center;gap:8px}
.pvg-status .pulse{width:8px;height:8px;border-radius:50%;background:var(--ledger);box-shadow:0 0 0 4px var(--ledger-soft);
  animation:pvgpulse 2.4s ease-in-out infinite}
@keyframes pvgpulse{50%{box-shadow:0 0 0 7px rgba(46,107,88,.08)}}
.st-key-topbar{margin:-18px 0 12px}
[class*="st-key-del_"] button [data-testid="stMarkdownContainer"]{display:none}
[class*="st-key-del_"] button{color:var(--ink-3)} [class*="st-key-del_"] button:hover{color:var(--redline)}
[data-testid="stExpander"] details{background:var(--sheet);border:0 !important;border-radius:14px;box-shadow:0 1px 0 var(--line)}
.st-key-topbar [data-testid="stPopover"] button{background:transparent;border:1px solid var(--line);color:var(--ink);border-radius:999px;min-height:38px}
.st-key-topbar [data-testid="stPopover"] button:hover{border-color:var(--ink-3)}
.pvg-acc{display:flex;align-items:center;gap:12px;margin-bottom:6px}
.pvg-acc .avatar{width:42px;height:42px;border-radius:50%;background:#C9D6CF;display:grid;place-items:center;font-weight:600;color:var(--ink)}
.pvg-acc b{display:block;font-size:15px;color:var(--ink)} .pvg-acc small{color:var(--ink-3);font-size:12.5px}
.pvg-banner{background:#FBF1CC;color:#5E4600;border-radius:12px;padding:10px 16px;font-size:13.5px;margin-bottom:16px}
/* ---------- Títulos de pantalla ---------- */
.pvg-title{font-size:30px;line-height:1.15;letter-spacing:-.7px;font-weight:650;color:var(--ink);margin:6px 0 6px}
.pvg-sub{font-size:15.5px;color:var(--ink-2);max-width:70ch;margin-bottom:10px}
.pvg-hello{font-size:14px;color:var(--ink-3);margin-bottom:2px}
/* ---------- Hero ---------- */
.pvg-hero{display:grid;grid-template-columns:1.45fr 1fr;gap:28px;align-items:stretch;margin:6px 0 26px}
.pvg-h1{font-size:34px;line-height:1.15;letter-spacing:-.8px;font-weight:650;max-width:22ch;margin-bottom:10px;color:var(--ink)}
.pvg-lede{font-size:16.5px;color:var(--ink-2);max-width:56ch;margin-bottom:24px;line-height:1.5}
.pvg-lede b{color:var(--ink);font-weight:600}
.pvg-page{position:relative;background:var(--sheet);border-radius:3px;padding:30px 36px 26px 44px;
  box-shadow:0 1px 0 var(--line), 0 18px 40px -22px rgba(21,36,31,.35)}
.pvg-page:before{content:"";position:absolute;left:22px;top:28px;bottom:26px;width:2px;background:var(--redline);opacity:.85}
.pvg-page .folio{position:absolute;right:26px;top:22px;font-family:var(--doc);font-size:13px;color:var(--ink-3)}
.pvg-page .section{font-family:var(--doc);font-style:italic;color:var(--ink-3);font-size:14px;margin-bottom:12px}
.pvg-page .quote{font-family:var(--doc);font-size:19.5px;line-height:2.05;color:#2A3833;max-width:52ch}
.pvg-page.small .quote{font-size:17px;line-height:2.2}
.pvg-page .hl{background:linear-gradient(transparent 12%, var(--marker) 12%, var(--marker) 88%, transparent 88%);padding:0 2px;
  background-size:0% 100%;background-repeat:no-repeat;animation:pvgmark .6s ease-out .25s forwards}
.pvg-page .old{position:relative;color:#8C9993;white-space:nowrap}
.pvg-page .old:after{content:"";position:absolute;left:-2px;right:-2px;top:54%;height:2.5px;border-radius:2px;background:var(--redline);
  transform:scaleX(0);transform-origin:left;animation:pvgstrike .5s cubic-bezier(.6,0,.3,1) .8s forwards}
.pvg-page .new{position:relative;color:var(--redline);font-weight:700;white-space:nowrap}
.pvg-page .new:after{content:attr(data-delta);position:absolute;left:50%;top:-1.15em;transform:translateX(-50%) rotate(-3deg);
  font-family:var(--ui);font-size:11px;line-height:1;font-weight:600;white-space:nowrap;color:var(--redline);
  opacity:0;animation:pvgfade .4s ease-out 1.3s forwards}
.pvg-page .fix{display:block;font-size:16px;margin-top:6px}
@keyframes pvgmark{to{background-size:100% 100%}}
@keyframes pvgstrike{to{transform:scaleX(1)}}
@keyframes pvgfade{to{opacity:1}}
.pvg-page .source{display:flex;gap:8px 18px;flex-wrap:wrap;margin-top:22px;padding-top:16px;border-top:1px dashed var(--line);font-size:13px;color:var(--ink-2)}
.pvg-page .source b{color:var(--ink);font-weight:600}
.pvg-page .verified{display:inline-flex;align-items:center;gap:6px;color:var(--ledger);font-weight:600}
.pvg-cost{background:var(--ink);color:#EAF0EC;border-radius:18px;padding:28px 30px;display:flex;flex-direction:column}
.pvg-cost .q{font-size:14px;color:#A9B8B2}
.pvg-cost .num{font-size:52px;font-weight:700;letter-spacing:-1.5px;line-height:1.05;margin:8px 0 4px;color:#fff}
.pvg-cost .num em{font-style:normal;color:#F19A8F}
.pvg-cost .per{font-size:15px;color:#C7D3CE;margin-bottom:26px}
.pvg-cost .split{display:flex;flex-direction:column;gap:16px}
.pvg-cost .row{display:grid;grid-template-columns:1fr auto;gap:6px 12px;align-items:baseline}
.pvg-cost .row .n{font-weight:600}
.pvg-cost .row .n small{font-weight:400;color:#93A6A0;margin-left:6px;font-size:13px}
.pvg-cost .row .v{font-weight:600}
.pvg-cost .bar{grid-column:1/-1;height:8px;border-radius:8px;background:#26392F;overflow:hidden}
.pvg-cost .bar i{display:block;height:100%;border-radius:8px;background:#F19A8F}
.pvg-cost .horizon{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:28px 0 22px}
.pvg-cost .horizon div{background:#1E3029;border-radius:12px;padding:14px 16px}
.pvg-cost .horizon span{display:block;font-size:13px;color:#93A6A0}
.pvg-cost .horizon b{display:block;font-size:22px;letter-spacing:-.4px;color:#fff;margin-top:2px}
.pvg-cost .formula{margin-top:auto;padding-top:18px;border-top:1px solid #2A3E35;font-size:13px;color:#93A6A0;line-height:1.55}
.pvg-cost .formula b{color:#EAF0EC;font-weight:600}
.pvg-cost .none{color:#93A6A0;font-size:14px}
/* ---------- Secciones, listas y tarjetas ---------- */
.pvg-h2{font-size:21px;letter-spacing:-.3px;font-weight:650;margin:14px 0 4px;color:var(--ink)}
.pvg-h2sub{color:var(--ink-2);font-size:14.5px;margin-bottom:8px}
.st-key-clientlist{gap:2px !important}
.pvg-item{display:grid;grid-template-columns:42px minmax(0,1fr) auto;gap:12px;align-items:center;padding:14px;border-radius:12px;transition:background .15s}
.pvg-item.sel{background:var(--sheet);box-shadow:inset 3px 0 0 var(--redline), 0 1px 0 var(--line)}
.pvg-item .ini{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;font-weight:600;background:#D9E3DD;color:var(--ink)}
.pvg-item .nm{display:block;font-weight:600;line-height:1.25;color:var(--ink);overflow-wrap:anywhere}
.pvg-item .pf{display:block;font-size:13px;color:var(--ink-3);line-height:1.45}
.pvg-item .amt{text-align:right;font-weight:650;color:var(--redline);white-space:nowrap}
.pvg-item .amt small{display:block;font-weight:500;font-size:12px;color:var(--ink-3)}
/* Columna de clientes estrecha: el importe pasa debajo del nombre en vez de salirse de la tarjeta */
.st-key-clientlist{container-type:inline-size}
@container (max-width:360px){.pvg-item{grid-template-columns:42px minmax(0,1fr)}.pvg-item .amt{grid-column:2;text-align:left}.pvg-item .amt small{display:inline;margin-left:5px}}
.pvg-chip{white-space:nowrap;display:inline-flex;align-items:center;gap:5px;margin-top:6px;font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px}
.pvg-chip.wait{background:#FBF1CC;color:#7A5B00}
.pvg-chip.ok{background:var(--ledger-soft);color:var(--ledger)}
.pvg-chip.red{background:var(--redline-soft);color:var(--redline)}
.pvg-chip.grey{background:#E6EAE6;color:var(--ink-2)}
.pvg-listnote{margin-top:12px;padding:14px;font-size:13px;color:var(--ink-3);border-top:1px solid var(--line)}
/* Tarjetas clicables: un botón invisible cubre toda la tarjeta. */
[class*="st-key-cli_"], [class*="st-key-pick_"]{position:relative;gap:0 !important;cursor:pointer}
[class*="st-key-cli_"] [data-testid="stElementContainer"], [class*="st-key-pick_"] [data-testid="stElementContainer"],
[class*="st-key-cli_"] .stButton, [class*="st-key-pick_"] .stButton,
[class*="st-key-cli_"] .stButton > div, [class*="st-key-pick_"] .stButton > div{position:static !important}
[class*="st-key-cli_"] [data-testid="stElementContainer"]:has(button),
[class*="st-key-pick_"] [data-testid="stElementContainer"]:has(button){height:0 !important;min-height:0;margin:0;overflow:visible}
[class*="st-key-cli_"] .stButton button, [class*="st-key-pick_"] .stButton button{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;z-index:3}
[class*="st-key-cli_"]:hover .pvg-item:not(.sel){background:rgba(255,255,255,.55)}
.st-key-detail, [class*="st-key-card_"]{background:var(--sheet);border-radius:18px;padding:28px;box-shadow:0 1px 0 var(--line)}
[class*="st-key-card_"]{padding:22px 24px}
.pvg-who2{display:flex;align-items:baseline;gap:12px;margin-bottom:4px;flex-wrap:wrap}
.pvg-who2 b{font-size:22px;letter-spacing:-.3px;color:var(--ink)}
.pvg-who2 span{color:var(--ink-3);font-size:14px}
.pvg-facts{display:flex;flex-wrap:wrap;gap:12px 26px;margin:16px 0 22px;padding-bottom:20px;border-bottom:1px solid var(--line)}
.pvg-facts div{font-size:13px;color:var(--ink-3)}
.pvg-facts b{display:block;font-size:19px;color:var(--ink);font-weight:650;letter-spacing:-.2px}
.pvg-facts .red b{color:var(--redline)}
.pvg-label{font-size:13.5px;font-weight:600;color:var(--ink-2);margin-bottom:8px}
.pvg-brief{font-size:15.5px;line-height:1.62;color:#2A3833;max-width:60ch;margin-bottom:22px}
.pvg-holdings{border:1px solid var(--line);border-radius:10px;overflow:hidden}
.pvg-holdings div{display:flex;justify-content:space-between;gap:12px;padding:10px 14px;font-size:14px;border-top:1px solid var(--line);color:var(--ink)}
.pvg-holdings div:first-child{border-top:0}
.pvg-holdings .hit{background:var(--redline-soft);color:var(--redline);font-weight:600}
.pvg-empty{background:var(--sheet);border-radius:18px;padding:40px;text-align:center;color:var(--ink-2);box-shadow:0 1px 0 var(--line)}
.pvg-empty b{display:block;font-size:19px;color:var(--ink);margin-bottom:4px}
.pvg-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:14px 0 22px}
.pvg-kpi{background:var(--sheet);border-radius:14px;padding:16px 18px;box-shadow:0 1px 0 var(--line)}
.pvg-kpi span{display:block;font-size:13px;color:var(--ink-3)}
.pvg-kpi b{display:block;font-size:24px;font-weight:650;letter-spacing:-.4px;color:var(--ink);margin-top:2px}
.pvg-kpi.red b{color:var(--redline)}
.pvg-bars{display:flex;flex-direction:column;gap:14px}
.pvg-bars .r{display:grid;grid-template-columns:1fr auto;gap:6px 12px;font-size:14px}
.pvg-bars .r b{font-weight:600;color:var(--ink)} .pvg-bars .r span{color:var(--ink-2)}
.pvg-bars .r small{color:var(--ink-3);margin-left:6px}
.pvg-bars .t{grid-column:1/-1;height:8px;border-radius:8px;background:#E6EBE6;overflow:hidden}
.pvg-bars .t i{display:block;height:100%;border-radius:8px;background:var(--ledger)}
.pvg-table{width:100%;border-collapse:collapse;font-size:14px}
.pvg-table, .pvg-table tr, .pvg-table th, .pvg-table td{border-left:0 !important;border-right:0 !important;border-top:0 !important;background:transparent}
.pvg-table th{text-align:left;font-weight:600;font-size:12.5px;color:var(--ink-3);padding:0 12px 10px;border-bottom:1px solid var(--line)}
.pvg-table td{padding:12px;border-bottom:1px solid #E7ECE7;color:var(--ink);vertical-align:middle}
.pvg-table tr:last-child td{border-bottom:0}
.pvg-table td.n{text-align:right;white-space:nowrap} .pvg-table th.n{text-align:right}
.pvg-table td small{display:block;color:var(--ink-3);font-size:12.5px}
.pvg-table td .pvg-chip{margin-top:0}
.pvg-tips{display:flex;flex-direction:column;gap:12px;margin:4px 0 18px}
.pvg-tips div{display:grid;grid-template-columns:28px 1fr;gap:10px;font-size:15px;line-height:1.55;color:#2A3833}
.pvg-tips i{font-style:normal;width:28px;height:28px;border-radius:50%;background:var(--ledger-soft);color:var(--ledger);
  display:grid;place-items:center;font-weight:700;font-size:13px}
.pvg-alertrow{display:grid;grid-template-columns:1fr auto;gap:6px 18px;align-items:center}
.pvg-alertrow b{font-size:16px;color:var(--ink)} .pvg-alertrow small{display:block;color:var(--ink-3);font-size:13px;margin-top:2px}
.pvg-alertrow q{display:block;font-family:var(--doc);font-style:italic;color:#2A3833;font-size:15px;margin-top:8px;grid-column:1/-1}
.pvg-postrow{display:grid;grid-template-columns:1fr auto;gap:2px 10px;align-items:center;font-size:14px;color:var(--ink)}
.pvg-postrow small{display:block;color:var(--ink-3);font-size:12.5px}
.st-key-postable [data-testid="stHorizontalBlock"]{align-items:center;padding:10px 0;border-bottom:1px solid #E7ECE7}
[data-testid="stForm"]{background:var(--sheet);border:0 !important;border-radius:18px;padding:22px 24px !important;box-shadow:0 1px 0 var(--line)}
/* ---------- Bienvenida ---------- */
.pvg-welcome{text-align:center;max-width:760px;margin:0 auto 30px}
/* Intro 3D: iframe fijo a pantalla completa, detrás del contenido */
.st-key-intro3d{position:fixed !important;inset:0;z-index:0;pointer-events:none}
.st-key-intro3d iframe{position:fixed !important;inset:0 !important;width:100vw !important;height:100vh !important;border:0;pointer-events:none}
.pvg-welcome, [class*="st-key-pick_"], .pvg-wfoot{position:relative;z-index:2}
.pvg-welcome .stage{height:280px;display:grid;place-items:center;margin-bottom:4px}
.pvg-welcome .stage .logo{margin:0 !important;transition:opacity .5s, transform .6s}
html.pvg-3d .pvg-welcome .stage .logo{opacity:0;transform:scale(.6)}
.pvg-welcome h1{text-shadow:0 0 24px #EEF1EC, 0 0 48px #EEF1EC}
.pvg-welcome p{text-shadow:0 0 18px #EEF1EC}
@keyframes pvgrise{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
@keyframes pvgrise2{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
/* Por defecto el contenido espera oculto (plan B: aparece a los 5 s aunque el 3D no cargue).
   La intro 3D pone html.pvg-reveal al terminar y entonces sube todo, escalonado. */
.pvg-welcome h1, .pvg-welcome p, .st-key-pick_gestor, .st-key-pick_particular, .pvg-wfoot{
  animation:pvgrise .9s cubic-bezier(.2,.8,.2,1) 5s both}
html.pvg-intro:not(.pvg-reveal) .pvg-welcome h1, html.pvg-intro:not(.pvg-reveal) .pvg-welcome p,
html.pvg-intro:not(.pvg-reveal) .st-key-pick_gestor, html.pvg-intro:not(.pvg-reveal) .st-key-pick_particular,
html.pvg-intro:not(.pvg-reveal) .pvg-wfoot{animation:none !important;opacity:0}
html.pvg-reveal .pvg-welcome h1{animation:pvgrise2 .9s cubic-bezier(.2,.8,.2,1) 0s both}
html.pvg-reveal .pvg-welcome p{animation:pvgrise2 .9s cubic-bezier(.2,.8,.2,1) .15s both}
html.pvg-reveal .st-key-pick_gestor{animation:pvgrise2 .9s cubic-bezier(.2,.8,.2,1) .3s both}
html.pvg-reveal .st-key-pick_particular{animation:pvgrise2 .9s cubic-bezier(.2,.8,.2,1) .45s both}
html.pvg-reveal .pvg-wfoot{animation:pvgrise2 .9s ease .7s both}
html.pvg-skip .pvg-welcome h1, html.pvg-skip .pvg-welcome p, html.pvg-skip .st-key-pick_gestor,
html.pvg-skip .st-key-pick_particular, html.pvg-skip .pvg-wfoot{animation:pvgrise2 .6s ease 0s both !important}
.pvg-welcome .logo{width:64px;height:64px;border-radius:18px;background:var(--ink);display:grid;place-items:center;margin:0 auto 22px}
.pvg-welcome .logo svg{width:36px;height:36px}
.pvg-welcome .logo svg path:first-child{fill:#EEF1EC} .pvg-welcome .logo svg path:last-child{stroke:#15241F}
.pvg-welcome h1{font-size:42px;line-height:1.1;letter-spacing:-1.2px;font-weight:700;color:var(--ink);margin:0 0 12px;padding:0}
.pvg-welcome p{font-size:17px;color:var(--ink-2);margin:0}
.pvg-pick{background:rgba(255,255,255,.86);backdrop-filter:blur(14px) saturate(1.15);-webkit-backdrop-filter:blur(14px);border-radius:20px;padding:30px 30px 26px;box-shadow:0 1px 0 var(--line), 0 18px 40px -26px rgba(21,36,31,.35);
  transition:transform .18s, box-shadow .18s;height:100%}
[class*="st-key-pick_"]:hover .pvg-pick{transform:translateY(-3px);box-shadow:0 1px 0 var(--line), 0 26px 50px -24px rgba(21,36,31,.45)}
.pvg-pick .ic{width:48px;height:48px;border-radius:14px;background:var(--ledger-soft);color:var(--ledger);display:grid;place-items:center;margin-bottom:18px}
.pvg-pick .ic svg{width:26px;height:26px}
.pvg-pick b{display:block;font-size:22px;letter-spacing:-.3px;color:var(--ink);margin-bottom:6px}
.pvg-pick p{color:var(--ink-2);font-size:15px;line-height:1.55;margin:0 0 16px}
.pvg-pick ul{margin:0 0 20px;padding:0;list-style:none;display:flex;flex-direction:column;gap:8px}
.pvg-pick li{font-size:14px;color:#2A3833;display:flex;gap:8px;align-items:flex-start}
.pvg-pick li:before{content:"✓";color:var(--ledger);font-weight:700}
.pvg-pick .go{display:inline-flex;font-weight:600;color:var(--ledger);font-size:15px}
/* ---------- Botones bajo el móvil ---------- */
[class*="st-key-actions"]{max-width:300px;margin:0 auto;gap:8px !important;width:100%}
[class*="st-key-actions"] button{min-height:44px;border-radius:12px !important;font-weight:600;font-size:14.5px}
[class*="st-key-actions"] button p{font-weight:600;font-size:14.5px}
[class*="st-key-actions"] [data-testid="stBaseButton-secondary"], [class*="st-key-actions"] [data-testid="stPopoverButton"]{
  background:var(--sheet);border:1px solid var(--line);color:var(--ink)}
[class*="st-key-actions"] [data-testid="stBaseButton-secondary"]:hover{border-color:var(--ink-3);color:var(--ink)}
[class*="st-key-actions"] [data-testid="stHorizontalBlock"]{gap:8px !important}
.pvg-approved{display:flex;align-items:center;justify-content:center;gap:8px;min-height:44px;border-radius:12px;
  background:var(--ledger-soft);color:var(--ledger);font-weight:600;font-size:14.5px;animation:pvgfade .3s ease-out both;margin-bottom:1rem}
/* ---------- Avatar ---------- */
[class*="st-key-fmt_"]{display:flex;justify-content:center}
[class*="st-key-fmt_"] [data-testid="stButtonGroup"]{background:#E6EBE6;border-radius:12px;padding:3px}
[class*="st-key-fmt_"] button{border:0 !important;border-radius:9px !important;min-height:34px;font-weight:600;background:transparent}
[class*="st-key-fmt_"] button[kind*="Active"], [class*="st-key-fmt_"] [data-testid*="Active"]{background:#fff !important;color:var(--ink) !important;box-shadow:0 1px 3px rgba(21,36,31,.15)}
[class*="st-key-avrenew_"]{display:none !important}
.pvg-avprep{background:linear-gradient(160deg,#F6F8F4,#E1EEE8);border-radius:18px;padding:22px;text-align:center;color:var(--ink-2);font-size:14px;line-height:1.5}
.pvg-avprep b{display:block;font-size:17px;color:var(--ink);margin-bottom:4px}
.pvg-voice{font-size:12.5px;color:var(--ink-3);text-align:center;margin-top:2px}
.pvg-foot{margin-top:34px;font-size:12.5px;color:var(--ink-3)}
/* ---------- Catálogo (vista_catalogo.py) ---------- */
.pvg-hint{display:flex;align-items:flex-start;gap:6px;font-size:12.5px;line-height:1.45;color:var(--ledger);font-weight:500;margin:-6px 0 10px}
.pvg-hint svg{flex:none;margin-top:2px}
.pvg-hint.amber{color:#7A5B00} .pvg-hint.red{color:var(--redline)}
.pvg-sum{display:flex;align-items:center;gap:10px 30px;flex-wrap:wrap;background:var(--sheet);border-radius:14px;padding:14px 20px;margin-bottom:12px;
  box-shadow:0 1px 0 var(--line);font-size:14px;color:var(--ink-2)}
.pvg-sum .t{display:flex;align-items:center;gap:8px;font-weight:600;color:var(--ink)}
.pvg-sum .t svg{width:16px;height:16px;color:var(--ledger)}
.pvg-sum b{font-size:18px;font-weight:650;color:var(--ink);letter-spacing:-.3px;margin-right:6px}
.pvg-sum .warn b{color:#9A6B00} .pvg-sum .new b{color:var(--ledger)}
.pvg-revok{display:flex;align-items:center;gap:8px;font-size:13.5px;color:var(--ledger);font-weight:500;margin:-8px 0 18px}
.st-key-review{background:#FBF1CC;border-radius:16px;padding:18px 22px;margin:-6px 0 22px;gap:10px !important}
.pvg-revhd b{display:block;font-size:16px;color:#3F2F00} .pvg-revhd span{font-size:13.5px;color:#5E4600}
.pvg-revrow{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start;background:rgba(255,255,255,.7);border-radius:12px;padding:12px 14px;margin-top:6px}
.pvg-revrow .tag{font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:999px;background:#F4DE8A;color:#5E4600;white-space:nowrap}
.pvg-revrow.red .tag{background:var(--redline-soft);color:var(--redline)}
.pvg-revrow b{display:block;font-size:14px;color:var(--ink)} .pvg-revrow span{font-size:13.5px;color:var(--ink-2)}
.st-key-review button{min-height:38px;border-radius:10px !important;font-weight:600}
.pvg-fic{display:flex;flex-direction:column;gap:14px}
.pvg-fic .hd{display:flex;gap:12px;align-items:center}
.pvg-fic .ico{width:44px;height:44px;border-radius:12px;background:var(--ledger-soft);color:var(--ledger);display:grid;place-items:center;
  flex:none;font-weight:700;font-size:12px;letter-spacing:.2px}
.pvg-fic .ico svg{width:22px;height:22px}
.pvg-fic .hd b{display:block;font-size:17px;letter-spacing:-.2px;color:var(--ink);line-height:1.25}
.pvg-fic .hd small{display:block;font-size:13px;color:var(--ink-2);margin-top:2px}
.pvg-fic dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 20px;margin:0}
.pvg-fic dt{font-size:12px;color:var(--ink-3);font-weight:500}
.pvg-fic dd{margin:2px 0 0;font-size:13.5px;line-height:1.45;color:var(--ink);overflow-wrap:anywhere}
.pvg-fic .money{background:var(--ink);color:#C7D3CE;border-radius:12px;padding:14px 16px;font-size:14.5px;line-height:1.5}
.pvg-fic .money b{color:#fff} .pvg-fic .money em{font-style:normal;font-weight:700;color:#fff;font-size:18px}
.pvg-fic .money small{display:block;font-size:12px;color:#93A6A0;margin-top:2px}
.pvg-fic .alert{border-radius:10px;padding:10px 14px;font-size:13.5px;line-height:1.5}
.pvg-fic .alert.red{background:var(--redline-soft);color:#8E2A20} .pvg-fic .alert.amber{background:#FBF1CC;color:#5E4600}
.pvg-fic .note{font-size:13px;color:var(--ink-2);line-height:1.5;background:var(--sheet-2);border-radius:10px;padding:10px 12px}
.pvg-fic .foot, .pvg-nov .src{display:flex;flex-wrap:wrap;align-items:center;gap:6px 16px;padding-top:12px;border-top:1px dashed var(--line);font-size:12.5px;color:var(--ink-3)}
.pvg-fic .foot a, .pvg-nov a, .pvg-table a{color:var(--ledger);font-weight:600;text-decoration:none}
.pvg-fic .foot a:hover, .pvg-nov a:hover, .pvg-table a:hover{text-decoration:underline}
.pvg-fic .foot .ok{display:inline-flex;align-items:center;gap:5px;color:var(--ledger);font-weight:600}
.pvg-fic .foot .fia{padding:1px 8px;border-radius:999px;background:#E6EAE6;color:var(--ink-2);font-weight:600}
.pvg-fic .foot .fia.baja, .pvg-fic .foot .fia.ficticio{background:#FBF1CC;color:#7A5B00}
/* Novedades: tarjetas en línea de tiempo, verdes (hecho real) o ámbar (simulación). Las alertas del equipo siguen en rojo. */
.st-key-novs{position:relative;padding-left:26px;gap:14px !important}
.st-key-novs:before{content:"";position:absolute;left:7px;top:10px;bottom:10px;width:2px;background:var(--line)}
[class*="st-key-nov_"]{position:relative;background:var(--sheet);border-radius:16px;padding:18px 22px 16px;box-shadow:0 1px 0 var(--line);gap:10px !important}
[class*="st-key-nov_"]:before{content:"";position:absolute;left:-25px;top:22px;width:12px;height:12px;border-radius:50%;
  background:var(--ledger);box-shadow:0 0 0 4px var(--paper)}
[class*="st-key-nov_sim"]:before{background:#D9A500}
[class*="st-key-nov_"] [data-testid="stAudio"]{max-width:420px}
[class*="st-key-nov_"] button{border-radius:999px !important;min-height:36px;font-weight:600}
.pvg-nov{margin-bottom:1rem}
.pvg-nov .top{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;font-size:12.5px;color:var(--ink-3);margin-bottom:10px}
.pvg-nov .tag{font-size:11.5px;font-weight:700;padding:3px 10px;border-radius:999px;letter-spacing:.2px}
.pvg-nov .tag.real{background:var(--ledger-soft);color:var(--ledger)}
.pvg-nov .tag.sim{background:#FBF1CC;color:#7A5B00;border:1px dashed #D9A500}
.pvg-nov .kind{font-weight:600;color:var(--ink-2)} .pvg-nov .when{margin-left:auto}
.pvg-nov .body{display:grid;grid-template-columns:1fr 190px;gap:20px;align-items:start}
.pvg-nov .tt{font-size:17px;font-weight:650;letter-spacing:-.2px;color:var(--ink);line-height:1.3}
.pvg-nov .chg{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px;margin:8px 0 6px;font-size:15px}
.pvg-nov .chg .old{color:var(--ink-3);text-decoration:line-through;text-decoration-color:var(--ink-3)}
.pvg-nov .chg .arr{color:var(--ink-3)} .pvg-nov .chg .new{font-weight:700;color:var(--ink)}
.pvg-nov .chg small{color:var(--ink-3);font-size:12.5px}
.pvg-nov .sum{font-size:14px;line-height:1.55;color:var(--ink-2);max-width:68ch}
.pvg-nov .simnote{margin-top:8px;font-size:12.5px;font-weight:600;color:#7A5B00}
.pvg-nov .src{margin-top:10px}
.pvg-nov .imp{border-radius:12px;padding:12px 14px;background:var(--sheet-2)}
.pvg-nov .imp b{display:block;font-size:22px;font-weight:700;letter-spacing:-.5px;line-height:1.1;color:var(--ink)}
.pvg-nov .imp span{display:block;font-size:13px;color:var(--ink-2)}
.pvg-nov .imp small{display:block;font-size:11.5px;color:var(--ink-3);margin-top:6px;line-height:1.4}
.pvg-nov .imp.up{background:var(--redline-soft)} .pvg-nov .imp.up b{color:var(--redline)}
.pvg-nov .imp.down{background:var(--ledger-soft)} .pvg-nov .imp.down b{color:var(--ledger)}
.pvg-nov .imp.amber{background:#FBF1CC} .pvg-nov .imp.amber b{color:#7A5B00}
[data-testid="stTabs"] [data-baseweb="tab-list"]{gap:4px;flex-wrap:wrap}
[data-testid="stTabs"] button[role="tab"]{font-weight:600}
/* ---------- Pantallas estrechas ---------- */
@media (max-width:1100px){ .pvg-hero{grid-template-columns:1fr} .pvg-kpis{grid-template-columns:repeat(2,1fr)} }
@media (max-width:760px){ .pvg-nov .body{grid-template-columns:1fr} .pvg-fic dl{grid-template-columns:1fr} .pvg-nov .when{margin-left:0} }
@media (max-width:760px){
  [data-testid="stAppViewContainer"]:has(.st-key-rail){padding-left:0;padding-bottom:70px}
  .st-key-rail{top:auto;right:0;width:100% !important;height:64px;flex-direction:row !important;justify-content:space-around;padding:10px 6px}
  .st-key-rail .mark{display:none} .st-key-rail .st-key-nav_ajustes{margin-top:0}
  .block-container, [data-testid="stMainBlockContainer"]{padding:18px 16px 32px !important}
  .pvg-h1{font-size:27px} .pvg-title{font-size:25px} .pvg-welcome h1{font-size:32px}
  .pvg-welcome .stage{height:210px}
  .pvg-cost .num{font-size:40px} .pvg-cost .horizon b{font-size:18px} .pvg-cost{padding:24px 20px}
  .pvg-page{padding:26px 20px 22px 32px} .pvg-page:before{left:14px}
  .pvg-page .quote{font-size:17px}
}
</style>
<div class="pvg-css"></div>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Bloques de HTML reutilizables
# --------------------------------------------------------------------------

def page_header(title: str, sub: str = "", hello: str = "") -> None:
    show(f'{f"<div class=pvg-hello>{esc(hello)}</div>" if hello else ""}'
         f'<div class="pvg-title">{esc(title)}</div>'
         f'{f"<div class=pvg-sub>{sub}</div>" if sub else ""}')


def document_html(alert: Alert, small: bool = False) -> str:
    """La hoja del folleto: sección, página, cita corregida y procedencia."""
    ev = alert.evidence
    section = SECTION_BY_EVENT.get(alert.event_type, "Extracto del documento")
    return f"""
<article class="pvg-page{' small' if small else ''}">
  <span class="folio">pág. {ev.page}</span>
  <div class="section">{esc(section)}</div>
  <div class="quote">{marked_quote(alert)}</div>
  <div class="source">
    <span class="verified">{ICON["check"]}Cita exacta del documento</span>
    <span>{esc(ev.document)}</span>
    <span>ISIN <b>{esc(alert.isin)}</b></span>
    <span>Detectado {when(alert.detected_at)}</span>
  </div>
</article>"""


def cost_html(question: str, total: float, per: str, rows: list[tuple[str, str, float]], alert: Alert,
              base_word: str = "dinero en el fondo") -> str:
    """Panel oscuro: total, una barra por fila (nombre, subtítulo, €), 5 y 10 años y cómo se calcula."""
    top = max((abs(r[2]) for r in rows), default=0) or 1
    bars = "".join(
        f'<div class="row"><span class="n">{esc(n)}<small>{esc(sub)}</small></span><span class="v">{eur(abs(v), 2)}</span>'
        f'<span class="bar"><i style="width:{max(abs(v) / top * 100, 3):.1f}%"></i></span></div>'
        for n, sub, v in rows
    )
    sign = "+" if total >= 0 else "−"
    return f"""
<aside class="pvg-cost">
  <span class="q">{esc(question)}</span>
  <div class="num"><em>{sign}</em>{eur(abs(total), 2)}</div>
  <div class="per">{per}</div>
  <div class="split">{bars or '<div class="none">Nadie tiene este producto en cartera.</div>'}</div>
  <div class="horizon">
    <div><span>En 5 años</span><b>{eur(abs(total) * 5, 2)}</b></div>
    <div><span>En 10 años</span><b>{eur(abs(total) * 10, 2)}</b></div>
  </div>
  <div class="formula"><b>Cómo se calcula:</b> {base_word} × {num(abs(alert.delta_pp))} puntos de
    {change_word(alert)}, cada año, sin contar el efecto compuesto. Lo calcula el sistema con los datos de la
    cartera; la IA no pone las cifras.</div>
</aside>"""


def bars_html(rows: list[tuple[str, float]], total: float) -> str:
    """Barras horizontales claras (reparto de la cartera)."""
    if not rows:
        return '<div class="pvg-listnote" style="border:0">Sin datos todavía.</div>'
    top = max(v for _, v in rows) or 1
    return '<div class="pvg-bars">' + "".join(
        f'<div class="r"><b>{esc(n)}<small>{v / total * 100:.0f} %</small></b><span>{eur(v)}</span>'
        f'<span class="t"><i style="width:{max(v / top * 100, 2):.1f}%"></i></span></div>'
        for n, v in rows) + "</div>"


# --------------------------------------------------------------------------
# Voz: ajustes, móvil con nota de voz y generación del audio
# --------------------------------------------------------------------------

def voice_id_for(ctx: Ctx, profile: str) -> str:
    return st.session_state.get(f"voice_{profile}") or ctx.default_voices.get(profile) \
        or voice.DEFAULT_VOICES["principiante"]


def voice_override(profile: str) -> dict:
    """Ajustes finos de la pantalla Voces (se conservan al cambiar de pantalla)."""
    d = voice.VOICE_SETTINGS.get(profile, voice.VOICE_SETTINGS["principiante"])
    s = st.session_state
    return {
        "stability": s.get(f"stab_{profile}", d["stability"]),
        "similarity_boost": s.get(f"sim_{profile}", d["similarity_boost"]),
        "style": s.get(f"sty_{profile}", d["style"]),
        "speed": s.get(f"spd_{profile}", d["speed"]),
        "use_speaker_boost": True,
    }


def waveform(seed: str, n: int = 34) -> list[int]:
    """Alturas (px) de la forma de onda. Salen del texto: cada mensaje tiene la suya."""
    h = hashlib.sha256(seed.encode()).digest() * 2
    return [8 + (b % 21) for b in h[:n]]


def phone_html(sender: str, sender_sub: str, message: str, audio: Optional[bytes],
               meta_right: str, autoplay: bool) -> str:
    """El móvil con la nota de voz (reproductor real, estilo WhatsApp)."""
    bars = "".join(f'<i style="height:{v}px"></i>' for v in waveform(message))
    src = f"data:audio/mpeg;base64,{base64.b64encode(audio).decode()}" if audio else ""
    play = ('<svg viewBox="0 0 24 24" fill="#fff"><path d="M7 4v16l13-8z"/></svg>' if audio else
            '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:transparent;font-family:Inter,system-ui,sans-serif;color:#15241F;display:flex;justify-content:center;
  padding:4px 0 26px;-webkit-font-smoothing:antialiased;font-feature-settings:"tnum" 1}}
.phone{{width:282px;height:520px;border-radius:44px;background:#0F1A16;padding:12px;box-shadow:0 30px 50px -28px rgba(21,36,31,.6)}}
.screen{{height:100%;border-radius:34px;background:#E6EDE8;overflow:hidden;display:flex;flex-direction:column}}
.notch{{height:26px;display:flex;justify-content:center;align-items:flex-end;background:#fff}}
.notch i{{width:92px;height:22px;border-radius:0 0 14px 14px;background:#0F1A16}}
.head{{display:flex;align-items:center;gap:10px;padding:10px 16px 12px;background:#fff;border-bottom:1px solid #DCE4DF}}
.av{{width:34px;height:34px;border-radius:50%;background:#2E6B58;color:#fff;display:grid;place-items:center;font-size:12px;font-weight:700;flex:none}}
.head b{{display:block;font-size:13.5px;font-weight:600}}
.head small{{font-size:11.5px;color:#7D8A85}}
.chat{{flex:1;padding:16px 12px;display:flex;flex-direction:column;gap:10px;justify-content:flex-end;overflow:hidden;
  background-image:radial-gradient(rgba(46,107,88,.07) 1px, transparent 1px);background-size:14px 14px}}
.day{{align-self:center;font-size:11px;color:#7D8A85;background:#fff;padding:2px 10px;border-radius:999px}}
.bubble{{align-self:flex-end;width:94%;background:#D7F0DF;border-radius:16px 16px 4px 16px;padding:10px 12px 6px;
  box-shadow:0 1px 0 rgba(0,0,0,.06);animation:pop .45s cubic-bezier(.2,.9,.3,1.2) both}}
.vn{{display:flex;align-items:center;gap:10px}}
.play{{width:34px;height:34px;border-radius:50%;background:#2E6B58;display:grid;place-items:center;flex:none;border:0;cursor:pointer;
  transition:transform .15s}}
.play:hover{{transform:scale(1.06)}} .play:disabled{{background:#9DB5AC;cursor:default;transform:none}}
.play svg{{width:15px;height:15px;margin-left:2px}} .play.on svg{{margin-left:0}}
.wave{{flex:1;display:flex;align-items:center;justify-content:space-between;height:30px;cursor:pointer}}
.wave i{{display:block;width:3px;border-radius:3px;background:#9BBDB0;transition:background .12s}}
.wave i.p{{background:#2E6B58}}
.off .wave i{{background:#B9CEC6}}
.spd{{font-size:10.5px;font-weight:700;color:#fff;background:#5E8C7C;border:0;border-radius:999px;padding:3px 7px;cursor:pointer;flex:none}}
.meta{{display:flex;justify-content:space-between;font-size:11px;color:#5E7A70;margin-top:4px}}
.meta .ok b{{color:#2C8FD6;font-weight:600}}
.tr{{align-self:flex-end;width:94%;background:#fff;border-radius:12px;padding:9px 11px;font-size:11.5px;line-height:1.5;color:#33433D;
  animation:pop .45s cubic-bezier(.2,.9,.3,1.2) .15s both}}
.tr em{{font-style:normal;font-weight:600;color:#7D8A85;display:block;font-size:10.5px;margin-bottom:2px}}
.tr p{{display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}}
.tr.open p{{-webkit-line-clamp:unset;max-height:230px;overflow-y:auto}}
.tr button{{border:0;background:none;color:#2E6B58;font:600 11px Inter,sans-serif;margin-top:3px;cursor:pointer;padding:0}}
@keyframes pop{{from{{opacity:0;transform:translateY(10px) scale(.97)}}to{{opacity:1;transform:none}}}}
</style></head><body>
<div class="phone"><div class="screen">
  <div class="notch"><i></i></div>
  <div class="head"><span class="av">{initials(sender)}</span><span><b>{esc(sender_sub)}</b><small>en línea</small></span></div>
  <div class="chat">
    <span class="day">Hoy</span>
    <div class="bubble {'' if audio else 'off'}">
      <div class="vn">
        <button class="play" id="pl" {'' if audio else 'disabled'} aria-label="Reproducir">{play}</button>
        <span class="wave" id="wv">{bars}</span>
        {'<button class="spd" id="sp">1×</button>' if audio else ''}
      </div>
      <div class="meta"><span id="tm">{'0:00' if audio else 'Nota de voz sin generar'}</span>{meta_right}</div>
    </div>
    <div class="tr" id="tr"><em>Transcripción</em><p>{esc(message)}</p><button id="more">Ver todo</button></div>
  </div>
</div></div>
{f'<audio id="au" preload="auto" src="{src}"></audio>' if audio else ''}
<script>
const tr=document.getElementById('tr'), more=document.getElementById('more');
const p=tr.querySelector('p'); if(p.scrollHeight<=p.clientHeight+2) more.style.display='none';
more.onclick=()=>{{tr.classList.toggle('open');more.textContent=tr.classList.contains('open')?'Ver menos':'Ver todo'}};
const a=document.getElementById('au');
if(a){{
  const pl=document.getElementById('pl'), tm=document.getElementById('tm'), wv=document.getElementById('wv'), sp=document.getElementById('sp');
  const bars=[...wv.children], PLAY=pl.innerHTML,
        PAUSE='<svg viewBox="0 0 24 24" fill="#fff"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>';
  const fmt=s=>{{s=Math.max(0,Math.floor(s||0));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}};
  const paint=()=>{{const r=a.duration?a.currentTime/a.duration:0;bars.forEach((b,i)=>b.classList.toggle('p',(i+.5)/bars.length<=r));
    tm.textContent=(a.currentTime>0?fmt(a.currentTime)+' / ':'')+fmt(a.duration)}};
  a.addEventListener('loadedmetadata',paint); a.addEventListener('timeupdate',paint);
  a.addEventListener('play',()=>{{pl.innerHTML=PAUSE;pl.classList.add('on')}});
  a.addEventListener('pause',()=>{{pl.innerHTML=PLAY;pl.classList.remove('on')}});
  a.addEventListener('ended',()=>{{a.currentTime=0;paint()}});
  pl.onclick=()=>a.paused?a.play():a.pause();
  wv.onclick=e=>{{if(!a.duration)return;const r=wv.getBoundingClientRect();a.currentTime=(e.clientX-r.left)/r.width*a.duration;paint()}};
  const speeds=[1,1.5,2];sp.onclick=()=>{{const n=speeds[(speeds.indexOf(a.playbackRate)+1)%speeds.length];a.playbackRate=n;sp.textContent=n+'×'}};
  {"a.play().catch(()=>{});" if autoplay else ""}
}}
</script></body></html>"""


def voice_phone(ctx: Ctx, message: str, profile: str, scope: str, sender: str, sender_sub: str,
                meta_right: str) -> Optional[bytes]:
    """Pinta el móvil con la nota de voz y, si aún no existe, el botón para generarla.
    Devuelve el MP3 (o None). `scope` distingue cada mensaje (alerta + destinatario)."""
    state = st.session_state
    voice_id = voice_id_for(ctx, profile)
    override = voice_override(profile)
    audio_key = (f"audio_{scope}_{voice_id}_{ctx.model_id}_"
                 f"{hashlib.md5((voice.settings_tag(override) + message).encode()).hexdigest()[:10]}")
    if audio_key not in state:
        cached = voice.cached_audio(message, voice_id, ctx.model_id, profile, override)
        if cached:
            state[audio_key] = cached
    audio = state.get(audio_key)
    autoplay = state.pop("autoplay", None) == audio_key

    st.iframe(phone_html(sender, sender_sub, message, audio, meta_right, autoplay), height=552)

    if not audio:
        with st.container(key=f"actions_gen_{slug(scope)}"):
            if st.button("Generar nota de voz", icon=":material/graphic_eq:", type="primary",
                         width="stretch", key=f"gen_{audio_key}", disabled=not ctx.api_key,
                         help=None if ctx.api_key else "Falta la clave de ElevenLabs en .streamlit/secrets.toml"):
                with st.spinner("Grabando la nota de voz con ElevenLabs…"):
                    try:
                        res = voice.synthesize(message, voice_id, profile, ctx.api_key, ctx.model_id, override)
                        state[audio_key] = res.audio
                        state["autoplay"] = audio_key
                        st.rerun()
                    except voice.VoiceError as exc:
                        st.error(str(exc), icon="🔇")
    return audio
