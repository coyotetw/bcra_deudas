import streamlit as st
import requests
import pandas as pd
import time
import urllib3
from datetime import date
from io import StringIO

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="Central de Deudores BCRA",
    page_icon="🏦",
    layout="wide",
)

# ── Constantes ────────────────────────────────────────────────────────────────

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


# Valores que NO son nombres reales
NOMBRES_INVALIDOS = {
    "", "sin datos", "sin denominación", "sin denominacion",
    "no encontrado", "sin nombre", "⚠ sin nombre",
    "sin denominaci\xc3\xb3n", "none", "nan",
}

def es_nombre_valido(nombre) -> bool:
    if nombre is None:
        return False
    s = str(nombre).strip().lower()
    return s != "" and s not in NOMBRES_INVALIDOS

class SSLAdapter(requests.adapters.HTTPAdapter):
    def send(self, request, **kwargs):
        kwargs["verify"] = False
        return super().send(request, **kwargs)

def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.mount("https://api.bcra.gob.ar", SSLAdapter())
    return s

SESSION = make_session()

# ── API + Procesamiento ───────────────────────────────────────────────────────

def fetch_historicas(cuit: str) -> dict:
    url = f"{BASE_URL}/Deudas/Historicas/{cuit}"
    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code == 404:
            return {"notFound": True, "_status": 404}
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "_status": resp.status_code, "_body": resp.text[:400]}
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


def procesar(cuit: str, data: dict) -> dict:
    base = {
        "cuit": cuit, "nombre": "", "situacion_num": None,
        "situacion_label": "Sin datos", "manchas_24m": None,
        "cant_manchas": None, "deuda_total": None, "error": None,
    }
    if data.get("notFound"):
        base["error"] = "404 — sin registros en BCRA"
        return base
    if data.get("error"):
        base["error"] = data["error"]
        return base
    results = data.get("results")
    if not results:
        base["error"] = f"results nulo (status={data.get('_status')})"
        return base

    base["nombre"] = (results.get("denominacion") or "").strip()
    periodos = results.get("periodos") or []

    if not periodos:
        base.update({"situacion_num": 1, "situacion_label": SIT_LABELS[1],
                     "manchas_24m": False, "cant_manchas": 0, "deuda_total": 0})
        return base

    max_sit, deuda = 0, 0.0
    for ent in (periodos[0].get("entidades") or []):
        sit = ent.get("situacion") or 0
        if sit > max_sit:
            max_sit = sit
        deuda += ent.get("monto") or 0.0

    base["situacion_num"]   = max_sit if max_sit > 0 else 1
    base["situacion_label"] = SIT_LABELS.get(base["situacion_num"], f"Sit. {base['situacion_num']}")
    base["deuda_total"]     = deuda

    manchas = sum(
        1 for p in periodos for e in (p.get("entidades") or [])
        if (e.get("situacion") or 0) > 1
    )
    base["manchas_24m"]  = manchas > 0
    base["cant_manchas"] = manchas
    return base


def parse_cuits(texto: str) -> list:
    vistos, resultado = set(), []
    for linea in texto.splitlines():
        c = linea.strip().replace("-", "").replace(" ", "")
        if c.isdigit() and 10 <= len(c) <= 11 and c not in vistos:
            resultado.append(c)
            vistos.add(c)
    return resultado


def fila_exportable(proc: dict, idx: int) -> dict:
    return {
        "#":                       idx,
        "CUIT_CUIL":               proc["cuit"],
        "Persona_Entidad":         proc["nombre"],
        "Situacion_Actual":        proc["situacion_label"],
        "Num_Situacion":           proc["situacion_num"] if proc["situacion_num"] is not None else "",
        "Manchas_24m_Sit_Mayor_1": ("Sí" if proc["manchas_24m"] is True else
                                    "No" if proc["manchas_24m"] is False else "—"),
        "Cant_Manchas":            proc["cant_manchas"] if proc["cant_manchas"] is not None else "",
        "Deuda_Total_ARS":         proc["deuda_total"]  if proc["deuda_total"]  is not None else "",
        "Error":                   proc["error"] or "",
    }


# ── TABS ──────────────────────────────────────────────────────────────────────

tab_consulta, tab_unificar, tab_checker = st.tabs([
    "🔍 Consulta masiva",
    "📎 Unificar CSVs",
    "🔧 Checker de incompletos",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — CONSULTA MASIVA
# ═══════════════════════════════════════════════════════════════════════════════

with tab_consulta:
    st.title("🏦 Central de Deudores — BCRA")
    st.caption("Consulta masiva · api.bcra.gob.ar · /Deudas/Historicas/")
    st.divider()

    col_izq, col_der = st.columns([2, 1])
    with col_izq:
        texto = st.text_area(
            "CUIT / CUIL / CDI — uno por línea (con o sin guiones)",
            placeholder="30527161319\n20123456789\n27-98765432-1",
            height=190,
            key="input_cuits",
        )
    with col_der:
        st.markdown("**Columnas devueltas:**")
        st.markdown("""
| Campo | Fuente |
|---|---|
| Persona / Entidad | `denominacion` |
| Situación actual | máx `situacion` en `periodos[0]` |
| Manchas 24m | ¿algún `situacion > 1` en 24 meses? |
| Deuda total | suma `monto` en `periodos[0]` |
""")
        st.caption("Una llamada por CUIT · ~1.1s entre consultas")

    col_btn, col_eta = st.columns([1, 3])
    with col_btn:
        btn = st.button("Consultar todos", type="primary", use_container_width=True, key="btn_consultar")
    with col_eta:
        eta_ph = st.empty()

    if btn:
        cuits = parse_cuits(texto)
        if not cuits:
            st.error("No se detectaron identificadores válidos (10–11 dígitos numéricos).")
            st.stop()

        secs = len(cuits) * (DELAY + 0.5)
        eta_ph.caption(f"⏱ ~{secs:.0f}s · {len(cuits)} identificador{'es' if len(cuits) > 1 else ''}")

        progress = st.progress(0.0)
        status   = st.empty()
        st.divider()
        tabla_ph = st.empty()

        filas, raw_store = [], {}

        for i, cuit in enumerate(cuits):
            status.markdown(f"Consultando **{i+1}/{len(cuits)}** — `{cuit}`")
            raw  = fetch_historicas(cuit)
            proc = procesar(cuit, raw)
            raw_store[cuit] = raw
            filas.append({
                "#":                   i + 1,
                "CUIT / CUIL":         proc["cuit"],
                "Persona / Entidad":   proc["nombre"] or "⚠ Sin nombre",
                "Situación actual":    proc["situacion_label"],
                "Manchas 24m (sit>1)": ("Sí" if proc["manchas_24m"] is True  else
                                        "No" if proc["manchas_24m"] is False else "—"),
                "Cant. manchas":       proc["cant_manchas"] if proc["cant_manchas"] is not None else "",
                "Deuda total ($)":     proc["deuda_total"]  if proc["deuda_total"]  is not None else "",
                "_sit_num":            proc["situacion_num"],
                "_manchas_bool":       proc["manchas_24m"],
                "_nombre_ok":          es_nombre_valido(proc["nombre"]),
                "_error":              proc["error"] or "",
                "_proc":               proc,
            })
            tabla_ph.dataframe(
                pd.DataFrame(filas)[[
                    "#", "CUIT / CUIL", "Persona / Entidad",
                    "Situación actual", "Manchas 24m (sit>1)",
                    "Cant. manchas", "Deuda total ($)"
                ]],
                use_container_width=True, hide_index=True,
            )
            progress.progress((i + 1) / len(cuits))
            if i < len(cuits) - 1:
                time.sleep(DELAY)

        n = len(cuits)
        sin_nombre = [f for f in filas if not f["_nombre_ok"]]
        status.success(
            f"✅ Completado — {n} procesados · "
            f"{n - len(sin_nombre)} con nombre · "
            f"{'⚠ ' + str(len(sin_nombre)) + ' sin nombre' if sin_nombre else '✓ todos con nombre'}"
        )

        # Resumen
        st.divider()
        st.subheader("Resumen")
        conteos = [
            ("Total",         n),
            ("Sit. 1",        sum(1 for f in filas if f["_sit_num"] == 1)),
            ("Sit. 2",        sum(1 for f in filas if f["_sit_num"] == 2)),
            ("Sit. 3–5",      sum(1 for f in filas if f["_sit_num"] and f["_sit_num"] >= 3)),
            ("Manchas 24m",   sum(1 for f in filas if f["_manchas_bool"] is True)),
            ("Sin nombre",    len(sin_nombre)),
            ("Errores",       sum(1 for f in filas if f["_error"])),
        ]
        for col, (lbl, val) in zip(st.columns(len(conteos)), conteos):
            col.metric(lbl, val)

        # Exportar
        st.divider()
        st.subheader("Exportar")
        hoy = date.today().isoformat()

        df_todos = pd.DataFrame([fila_exportable(f["_proc"], f["#"]) for f in filas])
        df_limpios = pd.DataFrame([
            fila_exportable(f["_proc"], i + 1)
            for i, f in enumerate(f2 for f2 in filas if f2["_nombre_ok"])
        ])
        df_sin_nombre = pd.DataFrame([{"CUIT_CUIL": f["CUIT / CUIL"]} for f in sin_nombre])

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**📄 Todos los resultados**")
            st.download_button(
                "⬇ CSV completo",
                data=df_todos.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"bcra_todos_{hoy}.csv",
                mime="text/csv", use_container_width=True,
            )
        with col_b:
            st.markdown("**✅ Solo con nombre (limpios)**")
            st.download_button(
                "⬇ CSV limpios",
                data=df_limpios.to_csv(index=False, encoding="utf-8-sig"),
                file_name=f"bcra_limpios_{hoy}.csv",
                mime="text/csv", use_container_width=True,
            )
        with col_c:
            st.markdown("**⚠ Sin nombre (para reintentar)**")
            if len(df_sin_nombre) > 0:
                st.download_button(
                    "⬇ CUITs a reintentar",
                    data=df_sin_nombre.to_csv(index=False, encoding="utf-8-sig"),
                    file_name=f"bcra_reintentar_{hoy}.csv",
                    mime="text/csv", use_container_width=True,
                )
                st.caption(f"{len(df_sin_nombre)} CUIT(s) sin nombre")
            else:
                st.success("¡Todos con nombre!")

        # Debug
        st.divider()
        with st.expander("🔍 Debug — JSON crudo"):
            sel = st.selectbox("CUIT", [f["CUIT / CUIL"] for f in filas], key="dbg")
            if sel and sel in raw_store:
                r = raw_store[sel]
                st.markdown(f"**HTTP status:** `{r.get('_status', 'N/A')}`")
                if r.get("error"):
                    st.error(f"❌ {r['error']}")
                    if r.get("_body"):
                        st.code(r["_body"])
                elif r.get("notFound"):
                    st.warning("404 — Sin registros en BCRA")
                else:
                    st.json({k: v for k, v in r.items() if not k.startswith("_")})


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — UNIFICAR CSVs
# ═══════════════════════════════════════════════════════════════════════════════

with tab_unificar:
    st.header("📎 Unificar múltiples CSVs")
    st.caption("Subí todos los archivos CSV generados por la consulta masiva. Se unifican y se eliminan duplicados por CUIT.")

    archivos = st.file_uploader(
        "Seleccioná uno o más archivos CSV",
        type=["csv"],
        accept_multiple_files=True,
        key="uploader_unificar",
    )

    if archivos:
        dfs = []
        errores_carga = []
        for archivo in archivos:
            try:
                df_temp = pd.read_csv(archivo, encoding="utf-8-sig")
                df_temp["_fuente"] = archivo.name
                dfs.append(df_temp)
            except Exception as e:
                errores_carga.append(f"{archivo.name}: {e}")

        if errores_carga:
            for err in errores_carga:
                st.error(f"❌ Error leyendo {err}")

        if dfs:
            df_unificado = pd.concat(dfs, ignore_index=True)
            total_antes  = len(df_unificado)

            # Detectar columna CUIT
            col_cuit = None
            for posible in ["CUIT_CUIL", "CUIT / CUIL", "cuit", "CUIT"]:
                if posible in df_unificado.columns:
                    col_cuit = posible
                    break

            if col_cuit:
                df_unificado[col_cuit] = df_unificado[col_cuit].astype(str).str.strip()
                df_unificado = df_unificado.drop_duplicates(subset=[col_cuit], keep="last")
                df_unificado = df_unificado.drop(columns=["_fuente"], errors="ignore")
                df_unificado = df_unificado.reset_index(drop=True)
                df_unificado.insert(0, "#", range(1, len(df_unificado) + 1))
                duplicados = total_antes - len(df_unificado)

                col_m1, col_m2, col_m3 = st.columns(3)
                col_m1.metric("Archivos cargados", len(archivos))
                col_m2.metric("Registros totales", total_antes)
                col_m3.metric("Duplicados eliminados", duplicados)

                st.dataframe(df_unificado, use_container_width=True, hide_index=True)

                hoy = date.today().isoformat()
                col_dl1, col_dl2 = st.columns(2)
                with col_dl1:
                    st.download_button(
                        "⬇ Descargar CSV unificado",
                        data=df_unificado.to_csv(index=False, encoding="utf-8-sig"),
                        file_name=f"bcra_unificado_{hoy}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                # También generar CSV de solo los limpios (con nombre)
                col_nombre = None
                for posible in ["Persona_Entidad", "Persona / Entidad", "nombre"]:
                    if posible in df_unificado.columns:
                        col_nombre = posible
                        break

                if col_nombre:
                    mask_limpios_u   = df_unificado[col_nombre].apply(es_nombre_valido)
                    df_limpios_u     = df_unificado[mask_limpios_u].reset_index(drop=True)
                    df_incompletos_u = df_unificado[~mask_limpios_u][[col_cuit]].reset_index(drop=True)

                    with col_dl2:
                        st.download_button(
                            "⬇ CSV solo limpios (con nombre)",
                            data=df_limpios_u.to_csv(index=False, encoding="utf-8-sig"),
                            file_name=f"bcra_unificado_limpios_{hoy}.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )

                    if len(df_incompletos_u) > 0:
                        st.warning(f"⚠ {len(df_incompletos_u)} CUIT(s) sin nombre en el archivo unificado")
                        st.dataframe(df_incompletos_u, use_container_width=True, hide_index=True)
                        st.download_button(
                            "⬇ CUITs incompletos (para reintentar)",
                            data=df_incompletos_u.to_csv(index=False, encoding="utf-8-sig"),
                            file_name=f"bcra_reintentar_{hoy}.csv",
                            mime="text/csv",
                        )
            else:
                st.error("No se encontró columna CUIT en los archivos. Verificá que sean CSVs generados por esta app.")
    else:
        st.info("👆 Subí los archivos CSV generados por la pestaña de consulta masiva.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CHECKER DE INCOMPLETOS
# ═══════════════════════════════════════════════════════════════════════════════

with tab_checker:
    st.header("🔧 Checker de CUITs incompletos")
    st.caption(
        "Subí un CSV (resultado previo o el archivo de 'reintentar') y la app detecta "
        "qué CUITs no tienen nombre, los vuelve a consultar y te devuelve el CSV actualizado."
    )

    archivo_check = st.file_uploader(
        "CSV a verificar",
        type=["csv"],
        key="uploader_checker",
    )

    if archivo_check:
        try:
            df_check = pd.read_csv(archivo_check, encoding="utf-8-sig")
        except Exception as e:
            st.error(f"Error leyendo el archivo: {e}")
            st.stop()

        # Detectar columnas
        col_cuit_ch = None
        for posible in ["CUIT_CUIL", "CUIT / CUIL", "cuit", "CUIT"]:
            if posible in df_check.columns:
                col_cuit_ch = posible
                break

        col_nombre_ch = None
        for posible in ["Persona_Entidad", "Persona / Entidad", "nombre"]:
            if posible in df_check.columns:
                col_nombre_ch = posible
                break

        if not col_cuit_ch:
            st.error("No se encontró columna CUIT en el archivo.")
            st.stop()

        df_check[col_cuit_ch] = df_check[col_cuit_ch].astype(str).str.strip()

        # Detectar incompletos: sin nombre o con nombre vacío/nulo/Sin datos
        if col_nombre_ch:
            mask_incompleto = ~df_check[col_nombre_ch].apply(es_nombre_valido)
        else:
            # Si el CSV solo tiene CUITs (archivo de reintentar), todos son incompletos
            mask_incompleto = pd.Series([True] * len(df_check))

        df_incompletos = df_check[mask_incompleto].copy()
        df_completos   = df_check[~mask_incompleto].copy()

        col_r1, col_r2, col_r3 = st.columns(3)
        col_r1.metric("Total en archivo",  len(df_check))
        col_r2.metric("Con nombre ✅",      len(df_completos))
        col_r3.metric("Sin nombre ⚠",      len(df_incompletos))

        if len(df_incompletos) == 0:
            st.success("✅ Todos los registros tienen nombre. No hay nada que reintentar.")
            st.dataframe(df_check, use_container_width=True, hide_index=True)
        else:
            st.warning(f"Se van a reintentar {len(df_incompletos)} CUITs sin nombre:")
            st.dataframe(
                df_incompletos[[col_cuit_ch]].rename(columns={col_cuit_ch: "CUIT a reintentar"}),
                use_container_width=True, hide_index=True,
            )

            btn_reintentar = st.button(
                f"🔄 Reintentar {len(df_incompletos)} CUITs ahora",
                type="primary",
                key="btn_reintentar",
            )

            if btn_reintentar:
                cuits_reintentar = [
                    c for c in df_incompletos[col_cuit_ch].tolist()
                    if str(c).isdigit() and 10 <= len(str(c)) <= 11
                ]

                if not cuits_reintentar:
                    st.error("No hay CUITs válidos para reintentar.")
                    st.stop()

                secs = len(cuits_reintentar) * (DELAY + 0.5)
                st.caption(f"⏱ ~{secs:.0f}s estimados")

                progress2 = st.progress(0.0)
                status2   = st.empty()
                nuevos_resultados = {}

                for i, cuit in enumerate(cuits_reintentar):
                    status2.markdown(f"Reintentando **{i+1}/{len(cuits_reintentar)}** — `{cuit}`")
                    raw  = fetch_historicas(cuit)
                    proc = procesar(cuit, raw)
                    nuevos_resultados[cuit] = proc
                    progress2.progress((i + 1) / len(cuits_reintentar))
                    if i < len(cuits_reintentar) - 1:
                        time.sleep(DELAY)

                status2.success(f"✅ Reintento completado — {len(cuits_reintentar)} CUITs")

                # Actualizar el dataframe original con los nuevos datos
                df_actualizado = df_check.copy()

                for cuit, proc in nuevos_resultados.items():
                    idx = df_actualizado[df_actualizado[col_cuit_ch] == cuit].index
                    if len(idx) == 0:
                        continue

                    if col_nombre_ch and proc["nombre"]:
                        df_actualizado.loc[idx, col_nombre_ch] = proc["nombre"]
                    if "Situacion_Actual" in df_actualizado.columns and proc["situacion_label"]:
                        df_actualizado.loc[idx, "Situacion_Actual"] = proc["situacion_label"]
                    if "Num_Situacion" in df_actualizado.columns and proc["situacion_num"] is not None:
                        df_actualizado.loc[idx, "Num_Situacion"] = proc["situacion_num"]
                    if "Manchas_24m_Sit_Mayor_1" in df_actualizado.columns and proc["manchas_24m"] is not None:
                        df_actualizado.loc[idx, "Manchas_24m_Sit_Mayor_1"] = (
                            "Sí" if proc["manchas_24m"] else "No"
                        )
                    if "Cant_Manchas" in df_actualizado.columns and proc["cant_manchas"] is not None:
                        df_actualizado.loc[idx, "Cant_Manchas"] = proc["cant_manchas"]
                    if "Deuda_Total_ARS" in df_actualizado.columns and proc["deuda_total"] is not None:
                        df_actualizado.loc[idx, "Deuda_Total_ARS"] = proc["deuda_total"]
                    if "Error" in df_actualizado.columns:
                        df_actualizado.loc[idx, "Error"] = proc["error"] or ""

                # Cuántos siguen sin nombre
                if col_nombre_ch:
                    aun_sin_nombre = df_actualizado[~df_actualizado[col_nombre_ch].apply(es_nombre_valido)]
                else:
                    aun_sin_nombre = pd.DataFrame()

                st.divider()
                if len(aun_sin_nombre) > 0:
                    st.warning(
                        f"⚠ Tras el reintento, {len(aun_sin_nombre)} CUIT(s) siguen sin nombre "
                        f"(posiblemente no tienen registro en el BCRA o la API no respondió)."
                    )
                else:
                    st.success("✅ ¡Todos los CUITs tienen nombre ahora!")

                st.dataframe(df_actualizado, use_container_width=True, hide_index=True)

                hoy = date.today().isoformat()
                col_x1, col_x2 = st.columns(2)

                with col_x1:
                    st.download_button(
                        "⬇ CSV actualizado (todos)",
                        data=df_actualizado.to_csv(index=False, encoding="utf-8-sig"),
                        file_name=f"bcra_actualizado_{hoy}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                if col_nombre_ch:
                    df_final_limpio = df_actualizado[df_actualizado[col_nombre_ch].apply(es_nombre_valido)].reset_index(drop=True)

                    with col_x2:
                        st.download_button(
                            "⬇ CSV limpio final (solo con nombre)",
                            data=df_final_limpio.to_csv(index=False, encoding="utf-8-sig"),
                            file_name=f"bcra_limpio_final_{hoy}.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )

                    if len(aun_sin_nombre) > 0:
                        st.download_button(
                            "⬇ CUITs que siguen sin nombre (para otro intento)",
                            data=aun_sin_nombre[[col_cuit_ch]].to_csv(index=False, encoding="utf-8-sig"),
                            file_name=f"bcra_reintentar_{hoy}.csv",
                            mime="text/csv",
                        )
    else:
        st.info("👆 Subí un CSV resultado de una consulta anterior para verificar los CUITs incompletos.")
        st.markdown("""
**¿Qué considera 'incompleto'?**
- Nombre vacío o nulo
- Nombre = `Sin datos`, `Sin denominación` o `No encontrado`
- CUITs con error en la consulta anterior

**Flujo recomendado:**
1. Hacés consulta masiva → bajás el CSV de *reintentar*
2. Lo subís acá → la app reintenta solo esos CUITs
3. Bajás el CSV limpio final
""")
