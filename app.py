import streamlit as st
import requests
import pandas as pd
import time
import json
from io import StringIO
from datetime import date

# ── Configuración de página ──────────────────────────────────────────────────

st.set_page_config(
    page_title="Central de Deudores BCRA",
    page_icon="🏦",
    layout="wide",
)

# ── Estilos ──────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap');

.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 99px;
    font-size: 12px;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
    white-space: nowrap;
}
.b1  { background: #e4f5ed; color: #1a7a52; }
.b2  { background: #fdf3dc; color: #8a5c0a; }
.b3  { background: #fdeaea; color: #8f2020; }
.b4  { background: #fdeaea; color: #8f2020; }
.b5  { background: #f8dada; color: #5c1212; }
.bnd { background: #f0f0ee; color: #888; }

.hist-ok  { color: #1a7a52; font-weight: 600; }
.hist-bad { color: #8f2020; font-weight: 600; }

.metric-container {
    background: #f8f8f6;
    border-radius: 10px;
    padding: 16px 20px;
    text-align: center;
    border: 1px solid #e0dfd8;
}
</style>
""", unsafe_allow_html=True)

# ── Constantes ───────────────────────────────────────────────────────────────

BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0"
DELAY    = 1.0   # segundos entre consultas
HEADERS  = {"Accept": "application/json"}

SIT_LABELS = {
    1: "1 — Normal",
    2: "2 — Seguimiento",
    3: "3 — Deficiente",
    4: "4 — Dudoso",
    5: "5 — Irrecuperable",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_cuits(texto: str) -> list[str]:
    """Parsea el texto y devuelve lista de CUITs únicos válidos."""
    cuits = []
    seen  = set()
    for linea in texto.splitlines():
        c = linea.strip().replace("-", "").replace(" ", "")
        if c and c.isdigit() and 10 <= len(c) <= 11 and c not in seen:
            cuits.append(c)
            seen.add(c)
    return cuits


def fetch_actual(cuit: str) -> dict:
    """Consulta la situación crediticia actual del CUIT."""
    try:
        r = requests.get(f"{BASE_URL}/Deudas/{cuit}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return {"notFound": True}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def fetch_historico(cuit: str) -> dict:
    """Consulta el historial de los últimos 24 meses."""
    try:
        r = requests.get(f"{BASE_URL}/Deudas/Historicas/{cuit}", headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return {"notFound": True}
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def procesar_actual(data: dict) -> tuple[str, int | None, float | None]:
    """
    Extrae nombre, situación máxima y deuda total del endpoint actual.
    La API devuelve: results.periodos[].entidades[] con campos directos:
      - situacion (int)
      - monto (float, en miles de pesos)
    """
    if data.get("notFound") or data.get("error") or not data.get("results"):
        return "—", None, None

    res      = data["results"]
    nombre   = res.get("denominacion", "—") or "—"
    periodos = res.get("periodos", [])

    max_sit     = 0
    deuda_total = 0.0

    for p in periodos:
        for e in p.get("entidades", []):
            # La API devuelve situacion y monto directamente en la entidad
            sit = e.get("situacion") or 0
            if sit > max_sit:
                max_sit = sit
            monto = e.get("monto")
            if monto:
                deuda_total += float(monto)

    # Si hay periodos pero todas las entidades tienen sit=0, asumimos 1 (normal)
    situacion = 1 if not periodos else (max_sit if max_sit > 0 else 1)
    return nombre, situacion, deuda_total if deuda_total > 0 else 0.0


def procesar_historico(data: dict, nombre_actual: str) -> tuple[str, int | None]:
    """
    Extrae nombre (si falta) y cantidad de períodos/entidades con situación > 1
    en el historial de 24 meses.
    La API devuelve la misma estructura que /Deudas pero con más períodos.
    """
    if data.get("notFound") or data.get("error") or not data.get("results"):
        return nombre_actual, None

    res      = data["results"]
    nombre   = res.get("denominacion") or nombre_actual
    periodos = res.get("periodos", [])

    manchas = 0
    for p in periodos:
        for e in p.get("entidades", []):
            sit = e.get("situacion") or 0
            if sit > 1:
                manchas += 1

    return nombre, manchas


def badge_html(situacion: int | None) -> str:
    if situacion is None:
        return '<span class="badge bnd">Sin datos</span>'
    cls   = f"b{min(situacion, 5)}"
    label = SIT_LABELS.get(situacion, f"Sit. {situacion}")
    return f'<span class="badge {cls}">{label}</span>'


def manchas_html(manchas: int | None) -> str:
    if manchas is None:
        return '<span style="color:#aaa">—</span>'
    if manchas == 0:
        return '<span class="hist-ok">No</span>'
    return f'<span class="hist-bad">Sí ({manchas} registro{"s" if manchas > 1 else ""})</span>'


def fmt_monto(monto: float | None) -> str:
    if monto is None or monto == 0:
        return "—"
    return f"$ {int(monto):,}".replace(",", ".")


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("🏦 Central de Deudores — BCRA")
st.caption("Consulta masiva · api.bcra.gob.ar · Situación actual + historial 24 meses")

st.divider()

# Textarea input
col_input, col_info = st.columns([2, 1])

with col_input:
    texto = st.text_area(
        "CUIT / CUIL / CDI",
        placeholder="20123456789\n27987654321\n30111222334\n(uno por línea, con o sin guiones)",
        height=180,
    )

with col_info:
    st.markdown("**Columnas que se muestran:**")
    st.markdown("""
- 🏷️ Persona / Entidad  
- 📊 Situación crediticia actual  
- 🔍 ¿Manchas en últimos 24 meses? (sit. >1)  
- 💰 Deuda total registrada  
""")
    st.caption("Tiempo estimado: ~2 segundos por CUIT")

# Botón
col_btn, col_eta = st.columns([1, 3])
with col_btn:
    consultar = st.button("Consultar todos", type="primary", use_container_width=True)
with col_eta:
    eta_placeholder = st.empty()

# ── Lógica de consulta ────────────────────────────────────────────────────────

if consultar:
    cuits = parse_cuits(texto)

    if not cuits:
        st.error("No se detectaron identificadores válidos. Verificá el formato (10–11 dígitos numéricos).")
    else:
        estimado = len(cuits) * 2.2
        unidad   = f"{estimado:.0f}s" if estimado < 60 else f"{estimado/60:.1f} min"
        eta_placeholder.caption(f"⏱ Estimado: {unidad} para {len(cuits)} identificadores")

        # Barra de progreso y estado
        progress_bar  = st.progress(0)
        status_text   = st.empty()

        # Tabla en tiempo real
        st.markdown("---")
        results_placeholder = st.empty()

        rows = []

        for i, cuit in enumerate(cuits):
            status_text.markdown(f"**Consultando {i+1}/{len(cuits)}** — `{cuit}`")

            # Consulta actual
            data_actual = fetch_actual(cuit)
            nombre, situacion, deuda = procesar_actual(data_actual)

            time.sleep(0.15)

            # Consulta histórico
            data_hist = fetch_historico(cuit)
            nombre, manchas = procesar_historico(data_hist, nombre)

            rows.append({
                "#":                   i + 1,
                "CUIT / CUIL":         cuit,
                "Persona / Entidad":   nombre,
                "_situacion_num":      situacion,
                "Situación actual":    SIT_LABELS.get(situacion, "Sin datos") if situacion else "Sin datos",
                "_manchas_num":        manchas,
                "Manchas 24m (sit>1)": "Sí" if manchas and manchas > 0 else ("No" if manchas == 0 else "Sin datos"),
                "Cant. registros":     manchas if manchas is not None else "",
                "Deuda total ($)":     int(deuda) if deuda and deuda > 0 else 0,
            })

            # Mostrar tabla parcial
            df_show = pd.DataFrame(rows)
            results_placeholder.dataframe(
                df_show[[
                    "#", "CUIT / CUIL", "Persona / Entidad",
                    "Situación actual", "Manchas 24m (sit>1)",
                    "Cant. registros", "Deuda total ($)"
                ]],
                use_container_width=True,
                hide_index=True,
            )

            progress_bar.progress((i + 1) / len(cuits))
            if i < len(cuits) - 1:
                time.sleep(DELAY)

        status_text.success(f"✅ Completado — {len(cuits)} identificador{'es' if len(cuits) > 1 else ''} procesado{'s' if len(cuits) > 1 else ''}")

        # ── Métricas resumen ─────────────────────────────────────────────────

        st.divider()
        st.subheader("Resumen")

        total  = len(rows)
        cnt_1  = sum(1 for r in rows if r["_situacion_num"] == 1)
        cnt_2  = sum(1 for r in rows if r["_situacion_num"] == 2)
        cnt_35 = sum(1 for r in rows if r["_situacion_num"] and r["_situacion_num"] >= 3)
        cnt_h  = sum(1 for r in rows if r["_manchas_num"] and r["_manchas_num"] > 0)
        cnt_nd = sum(1 for r in rows if r["_situacion_num"] is None)

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total",          total)
        m2.metric("Situación 1",    cnt_1,  delta=None)
        m3.metric("Situación 2",    cnt_2,  delta=None)
        m4.metric("Situación 3–5",  cnt_35, delta=None)
        m5.metric("Con manchas 24m", cnt_h, delta=None)
        m6.metric("Sin datos",      cnt_nd, delta=None)

        # ── Exportar ─────────────────────────────────────────────────────────

        st.divider()
        st.subheader("Exportar")

        df_export = pd.DataFrame([{
            "#":                          r["#"],
            "CUIT_CUIL":                  r["CUIT / CUIL"],
            "Persona_Entidad":            r["Persona / Entidad"],
            "Situacion_Actual":           r["Situación actual"],
            "Num_Situacion":              r["_situacion_num"] if r["_situacion_num"] else "",
            "Manchas_24m_Sit_Mayor_1":    r["Manchas 24m (sit>1)"],
            "Cant_Registros_Manchas":     r["Cant. registros"],
            "Deuda_Total_ARS":            r["Deuda total ($)"],
        } for r in rows])

        col_csv, col_json = st.columns(2)

        with col_csv:
            csv_data = df_export.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="⬇ Descargar CSV",
                data=csv_data,
                file_name=f"bcra_consulta_{date.today()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with col_json:
            json_data = df_export.to_json(orient="records", force_ascii=False, indent=2)
            st.download_button(
                label="⬇ Descargar JSON",
                data=json_data,
                file_name=f"bcra_consulta_{date.today()}.json",
                mime="application/json",
                use_container_width=True,
            )
