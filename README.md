# 🏦 Central de Deudores BCRA — Consulta masiva

Aplicación Streamlit para consultar la **Central de Deudores del BCRA** de forma masiva, ingresando una lista de CUIT/CUIL/CDI.

## Columnas que devuelve

| Columna | Descripción |
|---|---|
| **Persona / Entidad** | Nombre de la persona física o jurídica según el BCRA |
| **Situación actual** | 1 Normal · 2 Seguimiento · 3 Deficiente · 4 Dudoso · 5 Irrecuperable |
| **Manchas 24m (sit>1)** | Si registró situación mayor a 1 en los últimos 24 meses |
| **Cant. registros** | Cantidad de registros con situación >1 en el historial |
| **Deuda total ($)** | Suma de montos del período más reciente |

## Instalación y uso local

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_USUARIO/TU_REPO.git
cd TU_REPO

# 2. Crear entorno virtual (recomendado)
python -m venv venv
source venv/bin/activate      # Linux / Mac
venv\Scripts\activate         # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Ejecutar
streamlit run app.py
```

La app abre automáticamente en `http://localhost:8501`

## Deploy en Streamlit Cloud (gratis)

1. Subí este repositorio a GitHub
2. Entrá a [share.streamlit.io](https://share.streamlit.io)
3. Conectá tu cuenta de GitHub
4. Seleccioná el repo y el archivo `app.py`
5. Click en **Deploy** — listo, tenés una URL pública

## Notas

- La app aplica **~1 segundo de pausa** entre consultas para no ser bloqueada por Cloudflare
- Tiempo estimado: ~2.2 segundos por CUIT
- Sin límite publicado por el BCRA, pero se recomienda no superar las 500 consultas seguidas
- Los datos provienen de la API pública: `api.bcra.gob.ar`
- La información es suministrada por las entidades financieras. Su difusión no implica conformidad del BCRA

## Estructura del proyecto

```
├── app.py              # Aplicación principal
├── requirements.txt    # Dependencias
└── README.md
```
