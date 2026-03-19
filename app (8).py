import streamlit as st
import requests
import pandas as pd
import time
import ssl
from datetime import date
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Página ────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Central de Deudores BCRA",
    page_icon="🏦",
    layout="wide",
)

# ── Constantes — confirmadas desde el OpenAPI oficial del BCRA ────────────────
#
# Esquema HistorialEntidad (endpoint /Deudas/Historicas/):
#   entidad:   string
#   situacion: integer  ← campo DIRECTO (no es sub-array)
#   monto:     double   ← campo DIRECTO (no es sub-array)
#   enRevision: bool
#   procesoJud: bool
#
# Esquema HistorialPeriodo:
#   periodo:   string
#   entidades: HistorialEntidad[]
#
# Esquema HistorialDeuda:
#   identificacion: int64
#   denominacion:   string   ← nombre de la persona/empresa
#   periodos:       HistorialPeriodo[]   (el más reciente es el índice 0)

BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0"
DELAY    = 1.1

HEADERS = {
    "Accept":          "application/json",
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

# ── Sesión HTTP con SSL permisivo ─────────────────────────────────────────────
# El certificado de api.bcra.gob.ar tiene cadena incompleta.
# Usamos un adapter que deshabilita verificación para ese host específico.

class SSLAdapter(requests.adapters.HTTPAdapter):
    """Adapter que deshabilita verificación SSL para hosts con cert incompleto."""
    def send(self, request, **kwargs):
        kwargs["verify"] = False
        return super().send(request, **kwargs)

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    adapter = SSLAdapter()
    s.mount("https://api.bcra.gob.ar", adapter)
    return s

SESSION = make_session()

# ── API ───────────────────────────────────────────────────────────────────────

def fetch_historicas(cuit: str) -> dict:
    """
    GET /centraldedeudores/v1.0/Deudas/Historicas/{Identificacion}

    Retorna el JSON crudo de la API o dict con 'error'/'notFound'.
    Estructura exitosa:
      {
        "status": 200,
        "results": {
          "identificacion": <int>,
          "denominacion": <string>,
          "periodos": [
            {
              "periodo": "M/YYYY",
              "entidades": [
                {
                  "entidad": <string>,
                  "situacion": <int>,   <- CAMPO DIRECTO
                  "monto": <double>,    <- CAMPO DIRECTO
                  "enRevision": <bool>,
                  "procesoJud": <bool>
                }
              ]
            }
          ]
        }
      }
    """
    url = f"{BASE_URL}/Deudas/Historicas/{cuit}"
    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 404:
            return {"notFound": True, "_status": 404}
        if resp.status_code != 200:
            return {
                "error":   f"HTTP {resp.status_code}",
                "_status": resp.status_code,
                "_body":   resp.text[:400],
            }
        data = resp.json()
        data["_status"] = resp.status_code
        return data

    except requests.exceptions.SSLError as e:
        return {"error": f"SSL: {e}"}
    except requests.exceptions.Timeout:
        return {"error": "Timeout (15s)"}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"Conexión: {e}"}
    except Exception as e:
        return {"error": str(e)}


# ── Procesamiento ─────────────────────────────────────────────────────────────

def procesar(cuit: str, data: dict) -> dict:
    """
    Extrae los 4 campos requeridos del JSON de /Historicas/.
    Según el OpenAPI oficial, la estructura es:
      results.periodos[].entidades[].situacion  (int, campo directo)
      results.periodos[].entidades[].monto      (double, campo directo)
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
        base["nombre"] = "No encontrado"
        base["error"]  = "404 — sin registros en BCRA"
        return base

    if data.get("error"):
        base["error"] = data["error"]
        return base

    results = data.get("results")
    if not results:
        base["error"] = f"results nulo (status HTTP={data.get('_status')})"
        return base

    # denominacion
    base["nombre"] = (results.get("denominacion") or "").strip() or "Sin denominación"

    periodos = results.get("periodos") or []

    # Sin periodos = sin deudas → situación 1 limpia
    if not periodos:
        base.update({
            "situacion_num":   1,
            "situacion_label": SIT_LABELS[1],
            "manchas_24m":     False,
            "cant_manchas":    0,
            "deuda_total":     0,
        })
        return base

    # ── Situación actual = periodos[0] (el más reciente) ─────────────────────
    max_sit = 0
    deuda   = 0.0
    for ent in (periodos[0].get("entidades") or []):
        sit   = ent.get("situacion") or 0
        monto = ent.get("monto")     or 0.0
        if sit > max_sit:
            max_sit = sit
        deuda += monto

    base["situacion_num"]   = max_sit if max_sit > 0 else 1
    base["situacion_label"] = SIT_LABELS.get(base["situacion_num"], f"Sit. {base['situacion_num']}")
    base["deuda_total"]     = deuda

    # ── Manchas = cualquier entidad en cualquier período con situacion > 1 ────
    manchas = 0
    for periodo in periodos:
        for ent in (periodo.get("entidades") or []):
            if (ent.get("situacion") or 0) > 1:
                manchas += 1

    base["manchas_24m"]  = manchas > 0
    base["cant_manchas"] = manchas
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
st.caption("Consulta masiva · api.bcra.gob.ar · OpenAPI v1.0")
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
| Campo | Fuente en el JSON |
|---|---|
| Persona / Entidad | `results.denominacion` |
| Situación actual | máx `situacion` en `periodos[0].entidades[]` |
| Manchas 24m | ¿algún `situacion > 1` en los 24 períodos? |
| Deuda total | suma de `monto` en `periodos[0].entidades[]` |
""")
    st.caption("Endpoint: `/Deudas/Historicas/{CUIT}` · ~1.1s entre consultas")

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
    eta_ph.caption(
        f"⏱ ~{secs:.0f}s estimados · "
        f"{len(cuits)} identificador{'es' if len(cuits) > 1 else ''}"
    )

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
            "Manchas 24m (sit>1)": ("Sí" if proc["manchas_24m"] is True  else
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
    status.success(
        f"✅ Completado — {n} identificador{'es' if n > 1 else ''} "
        f"procesado{'s' if n > 1 else ''}"
    )

    # ── Resumen ───────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Resumen")

    conteos = [
        ("Total",          len(filas)),
        ("Sit. 1 Normal",  sum(1 for f in filas if f["_sit_num"] == 1)),
        ("Sit. 2",         sum(1 for f in filas if f["_sit_num"] == 2)),
        ("Sit. 3–5",       sum(1 for f in filas if f["_sit_num"] and f["_sit_num"] >= 3)),
        ("Manchas 24m",    sum(1 for f in filas if f["_manchas_bool"] is True)),
        ("Sin datos",      sum(1 for f in filas if f["_sit_num"] is None)),
        ("Errores",        sum(1 for f in filas if f["_error"])),
    ]
    for col, (lbl, val) in zip(st.columns(len(conteos)), conteos):
        col.metric(lbl, val)

    # ── Exportar ──────────────────────────────────────────────────────────────
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
    st.divider()
    with st.expander("🔍 Debug — JSON crudo de la API"):
        st.caption(
            "Si algún CUIT devuelve 'Sin datos' o error, seleccionalo acá "
            "para ver la respuesta exacta que devuelve la API del BCRA."
        )
        sel = st.selectbox("CUIT", [f["CUIT / CUIL"] for f in filas], key="dbg")
        if sel and sel in raw_store:
            r = raw_store[sel]
            st.markdown(f"**HTTP status:** `{r.get('_status', 'N/A')}`")
            if r.get("error"):
                st.error(f"❌ Error: {r['error']}")
                if r.get("_body"):
                    st.code(r["_body"])
            elif r.get("notFound"):
                st.warning("⚠️ 404 — El BCRA no tiene registros para este CUIT")
            else:
                clean = {k: v for k, v in r.items() if not k.startswith("_")}
                st.json(clean)
