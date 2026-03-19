import streamlit as st
import requests
import pandas as pd
import time
from datetime import date
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="Central de Deudores BCRA",
    page_icon="🏦",
    layout="wide",
)

BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0"
DELAY    = 1.1

HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "es-AR,es;q=0.9",
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":         "https://www.bcra.gob.ar/",
    "Origin":          "https://www.bcra.gob.ar",
}

SIT_LABELS = {
    1: "1 — Normal",
    2: "2 — Seguimiento especial",
    3: "3 — Con problemas",
    4: "4 — Alto riesgo de insolvencia",
    5: "5 — Irrecuperable",
}


def parse_cuits(texto: str) -> list:
    vistos, resultado = set(), []
    for linea in texto.splitlines():
        c = linea.strip().replace("-", "").replace(" ", "")
        if c.isdigit() and 10 <= len(c) <= 11 and c not in vistos:
            resultado.append(c)
            vistos.add(c)
    return resultado


def fetch_historicas(cuit: str) -> dict:
    url = f"{BASE_URL}/Deudas/Historicas/{cuit}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        if resp.status_code == 404:
            return {"notFound": True, "_status": 404}
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "_status": resp.status_code, "_body": resp.text[:300]}
        data = resp.json()
        data["_status"] = resp.status_code
        return data
    except requests.exceptions.SSLError as e:
        return {"error": f"SSL error: {e}"}
    except requests.exceptions.Timeout:
        return {"error": "Timeout (15s)"}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"Conexión: {e}"}
    except Exception as e:
        return {"error": str(e)}


def procesar(cuit: str, data: dict) -> dict:
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
        base["nombre"] = "No encontrado"
        base["error"]  = "404 — sin datos en BCRA"
        return base

    if data.get("error"):
        base["error"] = data["error"]
        return base

    results = data.get("results")
    if not results:
        base["error"] = f"results vacío (status={data.get('_status')})"
        return base

    base["nombre"] = (results.get("denominacion") or "").strip() or "Sin denominación"

    periodos = results.get("periodos") or []

    if not periodos:
        base.update({
            "situacion_num":   1,
            "situacion_label": SIT_LABELS[1],
            "manchas_24m":     False,
            "cant_manchas":    0,
            "deuda_total":     0,
        })
        return base

    # Situación actual = período más reciente (índice 0)
    entidades_hoy = periodos[0].get("entidades") or []
    max_sit = 0
    deuda   = 0
    for ent in entidades_hoy:
        sit   = ent.get("situacion") or 0
        monto = ent.get("monto")     or 0
        if sit > max_sit:
            max_sit = sit
        deuda += monto

    base["situacion_num"]   = max_sit if max_sit > 0 else 1
    base["situacion_label"] = SIT_LABELS.get(base["situacion_num"], f"Sit. {base['situacion_num']}")
    base["deuda_total"]     = deuda

    # Manchas = cualquier período con sit > 1
    manchas = 0
    for periodo in periodos:
        for ent in (periodo.get("entidades") or []):
            if (ent.get("situacion") or 0) > 1:
                manchas += 1

    base["manchas_24m"]  = manchas > 0
    base["cant_manchas"] = manchas
    return base


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("🏦 Central de Deudores — BCRA")
st.caption("Consulta masiva · api.bcra.gob.ar · /Deudas/Historicas/")
st.divider()

col_izq, col_der = st.columns([2, 1])

with col_izq:
    texto = st.text_area(
        "CUIT / CUIL / CDI — uno por línea (con o sin guiones)",
        placeholder="30527161319\n20123456789\n27-98765432-1",
        height=190,
    )

with col_der:
    st.markdown("**Columnas devueltas:**")
    st.markdown("""
| Columna | Descripción |
|---|---|
| Persona / Entidad | `denominacion` del BCRA |
| Situación actual | máx. `situacion` del período más reciente |
| Manchas 24m | ¿algún período tuvo `situacion > 1`? |
| Deuda total | suma de `monto` del período actual |
""")
    st.caption("Una llamada por CUIT · ~1.1s entre consultas")

col_btn, col_eta = st.columns([1, 3])
with col_btn:
    btn = st.button("Consultar todos", type="primary", use_container_width=True)
with col_eta:
    eta_ph = st.empty()

if btn:
    cuits = parse_cuits(texto)

    if not cuits:
        st.error("No se detectaron identificadores válidos (10–11 dígitos numéricos).")
        st.stop()

    secs = len(cuits) * (DELAY + 0.5)
    eta_ph.caption(f"⏱ ~{secs:.0f}s estimados · {len(cuits)} identificador{'es' if len(cuits) > 1 else ''}")

    progress = st.progress(0.0)
    status   = st.empty()
    st.divider()
    tabla_ph = st.empty()

    filas     = []
    raw_store = {}

    for i, cuit in enumerate(cuits):
        status.markdown(f"Consultando **{i+1}/{len(cuits)}** — `{cuit}`")

        raw  = fetch_historicas(cuit)
        proc = procesar(cuit, raw)
        raw_store[cuit] = raw

        filas.append({
            "#":                   i + 1,
            "CUIT / CUIL":         proc["cuit"],
            "Persona / Entidad":   proc["nombre"],
            "Situación actual":    proc["situacion_label"],
            "Manchas 24m (sit>1)": ("Sí" if proc["manchas_24m"] is True else
                                    "No" if proc["manchas_24m"] is False else "—"),
            "Cant. manchas":       proc["cant_manchas"] if proc["cant_manchas"] is not None else "",
            "Deuda total ($)":     proc["deuda_total"]  if proc["deuda_total"]  is not None else "",
            "_sit_num":            proc["situacion_num"],
            "_manchas_bool":       proc["manchas_24m"],
            "_error":              proc["error"] or "",
        })

        tabla_ph.dataframe(
            pd.DataFrame(filas)[[
                "#", "CUIT / CUIL", "Persona / Entidad",
                "Situación actual", "Manchas 24m (sit>1)",
                "Cant. manchas", "Deuda total ($)"
            ]],
            use_container_width=True,
            hide_index=True,
        )

        progress.progress((i + 1) / len(cuits))
        if i < len(cuits) - 1:
            time.sleep(DELAY)

    n = len(cuits)
    status.success(f"✅ Completado — {n} identificador{'es' if n > 1 else ''} procesado{'s' if n > 1 else ''}")

    # Resumen
    st.divider()
    st.subheader("Resumen")
    total, cnt_1, cnt_2, cnt_35, cnt_m, cnt_nd, cnt_err = (
        len(filas),
        sum(1 for f in filas if f["_sit_num"] == 1),
        sum(1 for f in filas if f["_sit_num"] == 2),
        sum(1 for f in filas if f["_sit_num"] and f["_sit_num"] >= 3),
        sum(1 for f in filas if f["_manchas_bool"] is True),
        sum(1 for f in filas if f["_sit_num"] is None),
        sum(1 for f in filas if f["_error"]),
    )
    for col, lbl, val in zip(
        st.columns(7),
        ["Total", "Sit. 1 Normal", "Sit. 2", "Sit. 3–5", "Manchas 24m", "Sin datos", "Errores"],
        [total, cnt_1, cnt_2, cnt_35, cnt_m, cnt_nd, cnt_err],
    ):
        col.metric(lbl, val)

    # Exportar
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

    # Debug
    st.divider()
    with st.expander("🔍 Debug — JSON crudo de la API"):
        st.caption("Si algún CUIT devuelve 'Sin datos', seleccionalo acá para ver la respuesta exacta de la API.")
        sel = st.selectbox("CUIT", [f["CUIT / CUIL"] for f in filas], key="dbg")
        if sel and sel in raw_store:
            raw_sel = raw_store[sel]
            st.markdown(f"**HTTP status:** `{raw_sel.get('_status', 'N/A')}`")
            if raw_sel.get("error"):
                st.error(f"Error: {raw_sel['error']}")
            elif raw_sel.get("notFound"):
                st.warning("404 — El BCRA no tiene datos para este CUIT")
            else:
                st.json({k: v for k, v in raw_sel.items() if not k.startswith("_")})
