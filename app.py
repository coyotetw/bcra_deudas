"""
Central de Deudores BCRA — Consulta masiva
==========================================
Estructura real de la API (documentación oficial api.bcra.gob.ar):

  GET /centraldedeudores/v1.0/Deudas/Historicas/{cuit}

  {
    "status": 200,
    "results": {
      "identificacion": 30527161319,
      "denominacion": "ESTANCIA LA PRADERA S.A.",
      "periodos": [
        {
          "periodo": "1/2026",
          "entidades": [
            {
              "entidad":   "BANCO DE LA NACION ARGENTINA",
              "situacion": 1,      <-- campo DIRECTO (int)
              "monto":     0,      <-- campo DIRECTO (int)
              ...
            }
          ]
        }
      ]
    }
  }

  - "situacion" y "monto" son campos DIRECTOS de cada entidad (NO sub-arrays)
  - /Historicas/ devuelve hasta 24 meses de historial
  - El período más reciente es el primero (índice 0) de la lista
  - Usamos SOLO este endpoint (contiene toda la info necesaria)
"""

import streamlit as st
import requests
import pandas as pd
import time
from datetime import date

# ── Página ────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Central de Deudores BCRA",
    page_icon="🏦",
    layout="wide",
)

# ── Constantes ────────────────────────────────────────────────────────────────

BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0"
HEADERS  = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
DELAY    = 1.1   # segundos entre consultas (evita bloqueo Cloudflare)

SIT_LABELS = {
    0: "0 — Sin información",
    1: "1 — Normal",
    2: "2 — Seguimiento especial",
    3: "3 — Con problemas",
    4: "4 — Con alto riesgo de insolvencia",
    5: "5 — Irrecuperable",
}

# ── Funciones de API ──────────────────────────────────────────────────────────

def fetch_historicas(cuit: str) -> dict:
    """
    Llama a /Deudas/Historicas/{cuit}.
    Retorna el JSON completo o un dict con clave 'error' / 'notFound'.
    """
    url = f"{BASE_URL}/Deudas/Historicas/{cuit}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            return {"notFound": True}
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}
        return resp.json()
    except requests.exceptions.Timeout:
        return {"error": "Timeout"}
    except Exception as e:
        return {"error": str(e)}


def procesar(cuit: str, data: dict) -> dict:
    """
    Extrae los 4 datos requeridos del JSON de /Historicas/:
      - nombre          (denominacion)
      - situacion_actual (max situacion en periodos[0])
      - manchas_24m     (True si alguna entidad en cualquier período tuvo sit > 1)
      - deuda_total     (suma de montos en periodos[0])
    """
    base = {
        "cuit":            cuit,
        "nombre":          "Sin datos",
        "situacion_num":   None,
        "situacion_label": "Sin datos",
        "manchas_24m":     None,
        "cant_manchas":    None,
        "deuda_total":     None,
        "error":           None,
    }

    if data.get("notFound"):
        base["error"] = "No encontrado (404)"
        return base

    if data.get("error"):
        base["error"] = data["error"]
        return base

    results = data.get("results")
    if not results:
        base["error"] = "Respuesta vacía"
        return base

    # Nombre de la persona/entidad
    base["nombre"] = (results.get("denominacion") or "").strip() or "Sin denominación"

    periodos = results.get("periodos") or []

    if not periodos:
        # Sin deudas registradas: situación 1, sin manchas
        base["situacion_num"]   = 1
        base["situacion_label"] = SIT_LABELS[1]
        base["manchas_24m"]     = False
        base["cant_manchas"]    = 0
        base["deuda_total"]     = 0
        return base

    # ── Situación actual = período MÁS RECIENTE (índice 0) ───────────────────
    entidades_hoy = periodos[0].get("entidades") or []
    max_sit_hoy   = 0
    deuda_hoy     = 0

    for ent in entidades_hoy:
        sit   = ent.get("situacion") or 0   # campo directo según esquema oficial
        monto = ent.get("monto")     or 0   # campo directo según esquema oficial
        if sit > max_sit_hoy:
            max_sit_hoy = sit
        deuda_hoy += monto

    # Si hay entidades pero todas sit=0 → asumir 1 (Normal)
    base["situacion_num"]   = max_sit_hoy if max_sit_hoy > 0 else 1
    base["situacion_label"] = SIT_LABELS.get(base["situacion_num"],
                                             f"Sit. {base['situacion_num']}")
    base["deuda_total"] = deuda_hoy

    # ── Manchas en los 24 meses = TODOS los períodos ─────────────────────────
    cant_manchas = 0
    for periodo in periodos:
        for ent in (periodo.get("entidades") or []):
            if (ent.get("situacion") or 0) > 1:
                cant_manchas += 1

    base["manchas_24m"]  = cant_manchas > 0
    base["cant_manchas"] = cant_manchas

    return base


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_cuits(texto: str) -> list:
    vistos, resultado = set(), []
    for linea in texto.splitlines():
        c = linea.strip().replace("-", "").replace(" ", "")
        if c.isdigit() and 10 <= len(c) <= 11 and c not in vistos:
            resultado.append(c)
            vistos.add(c)
    return resultado


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("🏦 Central de Deudores — BCRA")
st.caption("Consulta masiva · Fuente: api.bcra.gob.ar · endpoint: /Deudas/Historicas/")

st.divider()

col_left, col_right = st.columns([2, 1])

with col_left:
    texto = st.text_area(
        label="CUIT / CUIL / CDI — uno por línea (con o sin guiones)",
        placeholder="30527161319\n20123456789\n27-98765432-1",
        height=190,
    )

with col_right:
    st.markdown("**Columnas que devuelve:**")
    st.markdown("""
| Columna | Descripción |
|---|---|
| Persona / Entidad | `denominacion` |
| Situación actual | máx. `situacion` del período más reciente |
| Manchas 24m | ¿algún período/banco tuvo `situacion > 1`? |
| Deuda total | suma de `monto` del período actual |
""")
    st.caption("Una sola llamada API por CUIT · ~1.1s de espera entre consultas")

col_btn, col_eta = st.columns([1, 3])
with col_btn:
    btn = st.button("Consultar todos", type="primary", use_container_width=True)
with col_eta:
    eta_ph = st.empty()

# ── Ejecución ─────────────────────────────────────────────────────────────────

if btn:
    cuits = parse_cuits(texto)

    if not cuits:
        st.error("No se detectaron identificadores válidos (10–11 dígitos numéricos).")
        st.stop()

    secs = len(cuits) * (DELAY + 0.5)
    eta_ph.caption(
        f"⏱ ~{secs:.0f}s estimados para {len(cuits)} "
        f"identificador{'es' if len(cuits) > 1 else ''}"
    )

    progress = st.progress(0.0)
    status   = st.empty()
    st.divider()
    tabla_ph = st.empty()

    filas = []

    for i, cuit in enumerate(cuits):
        status.markdown(f"Consultando **{i+1} / {len(cuits)}** — `{cuit}`")

        raw  = fetch_historicas(cuit)
        proc = procesar(cuit, raw)

        filas.append({
            "#":                    i + 1,
            "CUIT / CUIL":          proc["cuit"],
            "Persona / Entidad":    proc["nombre"],
            "Situación actual":     proc["situacion_label"],
            "Manchas 24m (sit>1)":  ("Sí"  if proc["manchas_24m"] is True  else
                                     "No"  if proc["manchas_24m"] is False else "—"),
            "Cant. manchas":        proc["cant_manchas"] if proc["cant_manchas"] is not None else "",
            "Deuda total ($)":      proc["deuda_total"]  if proc["deuda_total"]  is not None else "",
            "_sit_num":             proc["situacion_num"],
            "_manchas_bool":        proc["manchas_24m"],
            "_error":               proc["error"] or "",
        })

        # Tabla actualizada en tiempo real
        df = pd.DataFrame(filas)
        tabla_ph.dataframe(
            df[["#", "CUIT / CUIL", "Persona / Entidad",
                "Situación actual", "Manchas 24m (sit>1)",
                "Cant. manchas", "Deuda total ($)"]],
            use_container_width=True,
            hide_index=True,
        )

        progress.progress((i + 1) / len(cuits))
        if i < len(cuits) - 1:
            time.sleep(DELAY)

    n = len(cuits)
    status.success(
        f"✅ Completado — {n} identificador{'es' if n > 1 else ''} "
        f"procesado{'s' if n > 1 else ''}"
    )

    # ── Resumen ──────────────────────────────────────────────────────────────

    st.divider()
    st.subheader("Resumen")

    total   = len(filas)
    cnt_1   = sum(1 for f in filas if f["_sit_num"] == 1)
    cnt_2   = sum(1 for f in filas if f["_sit_num"] == 2)
    cnt_35  = sum(1 for f in filas if f["_sit_num"] and f["_sit_num"] >= 3)
    cnt_m   = sum(1 for f in filas if f["_manchas_bool"] is True)
    cnt_nd  = sum(1 for f in filas if f["_sit_num"] is None)
    cnt_err = sum(1 for f in filas if f["_error"])

    cols = st.columns(7)
    for col, label, val in zip(cols, [
        "Total", "Sit. 1 Normal", "Sit. 2",
        "Sit. 3–5", "Con manchas 24m", "Sin datos", "Errores"
    ], [total, cnt_1, cnt_2, cnt_35, cnt_m, cnt_nd, cnt_err]):
        col.metric(label, val)

    # ── Exportar ─────────────────────────────────────────────────────────────

    st.divider()
    st.subheader("Exportar")

    hoy    = date.today().isoformat()
    df_exp = pd.DataFrame([{
        "CUIT_CUIL":               f["CUIT / CUIL"],
        "Persona_Entidad":         f["Persona / Entidad"],
        "Situacion_Actual":        f["Situación actual"],
        "Num_Situacion":           f["_sit_num"] if f["_sit_num"] is not None else "",
        "Manchas_24m_Sit_Mayor_1": f["Manchas 24m (sit>1)"],
        "Cant_Manchas":            f["Cant. manchas"],
        "Deuda_Total_ARS":         f["Deuda total ($)"],
        "Error":                   f["_error"],
    } for f in filas])

    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            "⬇ Descargar CSV",
            data=df_exp.to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"bcra_{hoy}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with col_b:
        st.download_button(
            "⬇ Descargar JSON",
            data=df_exp.to_json(orient="records", force_ascii=False, indent=2),
            file_name=f"bcra_{hoy}.json",
            mime="application/json",
            use_container_width=True,
        )

    # ── Debug ─────────────────────────────────────────────────────────────────

    with st.expander("🔍 Debug — ver JSON crudo de la API"):
        st.caption(
            "Mostrá el JSON que devuelve la API para verificar la estructura real. "
            "Si el nombre o la situación no aparecen, acá podés ver por qué."
        )
        cuit_debug = st.selectbox(
            "Seleccionar CUIT",
            options=[f["CUIT / CUIL"] for f in filas],
            key="debug_select",
        )
        if cuit_debug:
            with st.spinner("Consultando..."):
                st.json(fetch_historicas(cuit_debug))
