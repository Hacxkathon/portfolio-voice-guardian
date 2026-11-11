# Catálogo de Portfolio Voice Guardian

Base de datos de referencia para que la app sepa **qué es cada producto, dónde se compra, cuánto cuesta y qué ha cambiado**.
Revisado el 10/11/2026.

## Qué contiene

| Tabla | Filas | Para qué la usa la app |
|---|---|---|
| `instrumentos` | 93 | Autocompletar al añadir una posición, rellenar ISIN, ticker, tipo, emisor y comisión (TER) |
| `plataformas` | 22 | Comisiones y condiciones de cada bróker, banco, roboadvisor y exchange |
| `tipos_producto` | 8 | Qué documento oficial vigilamos de cada tipo de producto y dónde se publica |
| `eventos` | 11 | Alertas: 8 cambios **reales** con fuente + 3 **simulados** para la demo |

**Instrumentos**: 27 acciones del Mercado Continuo español, 21 acciones de EEUU y Europa, 16 ETF (MSCI World, S&P 500,
FTSE All-World, emergentes, Nasdaq-100, EURO STOXX 50, bonos, oro), 14 fondos (indexados y value españoles),
6 criptomonedas, 7 tipos de deuda del Tesoro y 2 cuentas remuneradas.

**Plataformas**: los mismos 21 nombres de `store.PLATAFORMAS`, más N26.

## Por qué SQLite + CSV (y no solo un Excel)

- **`catalogo.db` (SQLite)**: es lo que lee la app. Es el mismo formato que la base de datos de Jorge, es rápido
  y no necesita instalar nada. Python ya incluye `sqlite3`.
- **`datos/*.csv`**: son la fuente editable. Se abren con **Excel** (separador `;`, decimales con coma).
  Si cambias algo, ejecuta `python -m catalogo.construir` y la base de datos se regenera.
- **Sin pandas**: solo usa la librería estándar, así que Smart App Control no lo bloquea.

## Cómo se usa desde la app

Copia la carpeta `catalogo/` dentro de `pvg_ui/` y luego:

```python
import catalogo

catalogo.buscar("repsol")                    # [{'nombre_corto': 'Repsol', 'isin': 'ES0173516115', 'tipo': 'Acción', ...}]
catalogo.por_isin("IE00BK5BQT80")            # ficha completa del ETF (TER 0,14 %)
catalogo.isin_valido("ES00000000001")        # False
catalogo.plataforma("Openbank")              # comisiones, custodia, cambio de divisa, avisos...
catalogo.plataformas_activas_en_espana()     # para el desplegable (sin Binance)
catalogo.documento_para_tipo("Acción")       # qué vigilamos y en qué web oficial
catalogo.eventos_para(isins=["ES0173516115"], plataformas=["Openbank"])
catalogo.comprobar_cartera(posiciones)       # avisos: ISIN inválido, plataforma que ya no opera...
```

Todas devuelven `dict` o listas de `dict`. Si `catalogo.db` no existe, se crea sola a partir de los CSV.

### Tres sitios donde conectarlo (para Claude Code)

1. **Mi cartera → añadir producto** (`vista_particular._add_form`): sustituir el campo libre "Nombre del producto"
   por un buscador con `catalogo.buscar()`. Al elegir un resultado se rellenan solos el ISIN, el tipo y la comisión
   (`ter_pct`). El desplegable de plataformas puede salir de `catalogo.plataformas_activas_en_espana()`.
2. **Qué vigilamos** (`vista_particular.documentos`): usar `catalogo.documento_para_tipo(tipo)` en lugar de
   `store.DOCUMENTO_POR_TIPO`, que también da la web oficial donde se publica.
3. **Mis avisos** (`vista_particular.matches_for`): añadir los eventos de `catalogo.eventos_para(...)` con los ISIN
   y plataformas del usuario. Los eventos con `event_type` `fees` o `dividends` traen `delta_pp`, así que
   `ui.impact_eur()` ya sabe calcular su coste en euros. Los de plataforma (`condiciones`, `regulacion`) son avisos
   informativos.

**Importante:** los eventos con `es_simulado = 1` empiezan por `[SIMULACIÓN]` y la app debe mostrarlos como tal.
El de Repsol (recorte de dividendo) **no es una noticia real**: es un escenario para probar la demo.

## Cosas que hay que saber (encontradas al revisar)

1. **El Repsol de tu cartera tiene un ISIN mal escrito**: `ES00000000001` tiene 13 caracteres. El real es
   **`ES0173516115`**. Con el ISIN mal puesto, nunca te llegaría una alerta de Repsol. `comprobar_cartera()` lo detecta.
2. **Aena cambió de ISIN**: la lista oficial de la Agencia Tributaria de 2026 usa `ES0105046017`
   (antes `ES0105046009`).
3. **El `seed.py` de Jorge usa el ISIN real de CaixaBank** (`ES0140609019`) para la empresa ficticia
   "Iberia Telecom Infra SA". Quien tenga CaixaBank recibiría las alertas de la empresa inventada.
   Conviene que Jorge le ponga un ISIN ficticio.
4. **Binance ya no opera en España**: desde el 1/7/2026 (fin del periodo transitorio de MiCA) no admite clientes
   españoles. Está marcada con `opera_en_espana = 0`.
5. **Cambios reales recientes** incluidos como eventos: Vanguard FTSE All-World baja su TER del 0,19 % al 0,14 %;
   Revolut pasa a 1 € por orden; N26 vuelve a cobrar 0,90 €; Openbank baja a 1 €, elimina la custodia y sube
   el cambio de divisa del 0,70 % al 1 % (10/11/2026).

## Calidad de los datos

Cada fila lleva `fuente_url`, `verificado` (fecha) y `fiabilidad`:

- **alta**: fuente oficial (BME, CNMV, Agencia Tributaria, nota de prensa de la entidad) o varias fuentes coherentes.
- **media**: comparadores o prensa financiera. En las acciones extranjeras, el ISIN no se ha contrastado con una
  fuente externa, aunque pasa la validación del dígito de control.
- **baja**: datos dispares o no verificados (DEGIRO, Interactive Brokers, eToro). Hay que consultar su tarifa oficial.
- **ficticio**: el fondo de la demo del equipo (`ES0000000001`).

Las comisiones y los tipos de interés cambian a menudo. Antes de la presentación, revisa las filas con fiabilidad
media o baja. Este catálogo es información, no una recomendación de inversión.

**No incluye precios**: las cotizaciones cambian cada segundo y deben venir de una fuente en vivo.
