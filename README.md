# Alerts Service - SMN

<img src="https://uptime.mapasmn.com/api/badge/11/status?style=flat-square" /> <img src="https://uptime.mapasmn.com/api/badge/11/uptime?style=flat-square" /> <img src="https://uptime.mapasmn.com/api/badge/11/ping?style=flat-square" />

Servicio de intersección geográfica para el sistema de alertas meteorológicas. Provee endpoints REST para calcular intersecciones de polígonos con el territorio argentino y sus departamentos, utilizando datos del Instituto Geográfico Nacional (IGN).

### Team members

| Name                        | Padrón | Email                 |
| --------------------------- | ------ | --------------------- |
| Altamirano, Agustín Gabriel | 110237 | <aaltamirano@fi.uba.ar> |
| Diem, Walter Gabriel        | 105618 | <wdiem@fi.uba.ar>       |
| Gismondi, Máximo            | 110119 | <magismondi@fi.uba.ar>  |
| Valeriani, Matías Gabriel   | 108570 | <mvaleriani@fi.uba.ar>  |

### Table of Contents

1. [Features](#features)
1. [Dependencies](#dependencies)
1. [Setup for Development](#setup-for-development)
1. [Configuration](#configuration)
1. [API Documentation](#api-documentation)
1. [Running Tests](#running-tests)
1. [Makefile Commands](#makefile-commands)

## Features

- **Intersección geográfica**: `POST /intersect/country` (territorio argentino, con nivel de detalle 1-5) y `POST /intersect/departments` (departamentos que tocan el polígono).
- **Generación de avisos**: `POST /alerts` encola el render del GIF del aviso y devuelve `202` con un `job_id`; el resultado se consulta en `GET /alerts/jobs/{job_id}` y aparece en `GET /alerts/pending`.
- **Lectura de avisos vigentes**: `GET /alerts` y `GET /alerts/pending` leen las tablas MySQL `taviso` (externa, solo lectura) y `taviso_temporal`, con `ETag` / `If-None-Match` para respuestas `304`.
- **Capas pre-simplificadas**: una capa por nivel de detalle, generada con la tolerancia fijada en `settings.json` (`detail_level_tolerances`) y cacheada en memoria.
- **Gestión de capas**: al iniciar se reconcilian los archivos locales de `data/` contra S3 y se descarga del IGN lo que falte; un cron de APScheduler (`0 3 * * 0`, domingos 3 AM) regenera y vuelve a subir las capas.
- **Métricas**: `GET /metrics/*` expone agregados de los jobs de generación y del estado del procesador, persistidos en SQLite.
- **Dockerizado**: `docker-compose-dev.yaml` (hot-reload) y `docker-compose.yaml` (producción), ambos con el servicio MySQL incluido.

## Dependencies

Para ejecutar el proyecto no es necesario tener `python` instalado, ya que el proyecto está completamente Dockerizado. Solo es requerido para ejecutar el proyecto de forma nativa.

Dependencias necesarias:

- **Docker**: para ejecutar el proyecto en un entorno contenerizado
- **Make**: para simplificar y automatizar comandos
- **Python v3.13+**: solo si decides ejecutar la aplicación de forma nativa (sin Docker). `pyproject.toml` declara `requires-python = ">=3.13,<4.0"`

## Setup for Development

1. Clona el repositorio:

   ```bash
   git clone https://github.com/fiuba-tp-g153-smn/alerts-service.git
   cd alerts-service
   ```

2. Copia el archivo de configuración de ejemplo:

   ```bash
   cp .env.example .env
   ```

   Edita `.env` para configurar variables de entorno (puerto, logs, credenciales de MySQL y S3).

3. Levanta el entorno de desarrollo:

   ```bash
   make up
   ```

   La aplicación queda expuesta en el puerto `APP_HOST_PORT` del host (`6007` en `.env.example`), mapeado al `8080` del contenedor: <http://localhost:6007/docs>

## Configuration

La configuración se reparte entre `.env` (variables de entorno, credenciales y rutas) y `settings.json` (montado en `/config/settings.json`, para valores que se cambian sin rebuild). Los defaults viven en `src/settings.py`.

### Variables de Entorno

```bash
# Puerto del servicio en el host (el contenedor siempre escucha en 8080)
APP_HOST_PORT=6007

# Entorno de ejecución (production activa el logging JSON de NewRelic)
APP_ENV=development

# Nivel de logging
LOG_LEVEL=INFO

# Ruta al archivo settings.json dentro del contenedor
SETTINGS_FILE=/config/settings.json
```

`.env.example` incluye además las credenciales de S3 (`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME`, `S3_SECURE`), las de MySQL (base propia y `taviso` externa de solo lectura), `MANAGE_DB_SCHEMAS` —que habilita o inhibe todas las migraciones de Alembic— y los directorios `OUTPUT_DIR` / `ALERT_CACHE_DIR`. Las URLs WFS del IGN (`COUNTRY_GEOJSON_URL`, `DEPARTMENTS_GEOJSON_URL`, `PROVINCES_GEOJSON_URL`) tienen default en el código y solo se declaran para sobrescribirlas.

### Tolerancias de simplificación

`settings.json` define la tolerancia de simplificación por nivel de detalle, en grados. Menor tolerancia significa más detalle y archivos más grandes:

| `detail_level` | Tolerancia |
| -------------- | ---------- |
| 1              | 0.2        |
| 2              | 0.1        |
| 3              | 0.05       |
| 4              | 0.025      |
| 5              | 0.01       |
| 7              | 0.005      |

Los niveles 1 a 5 son los que acepta la API; el 7 es interno y lo usa la generación de avisos (`alerts.detail_level`). Los departamentos y las capas base del IGN se simplifican con una tolerancia fija de `0.005` (`departments_simplify_tolerance`, `ign_simplify_tolerance`).

### Geographic Data

El servicio gestiona automáticamente los datos geográficos:

1. **Al iniciar**: compara los archivos de `data/` contra S3 por fecha, descarga lo que falte y regenera desde el IGN lo que no esté en ninguno de los dos
2. **Simplificación**: genera un GeoJSON por nivel de detalle, fechado y con la tolerancia en el nombre (por ejemplo `pais_simple_L5_T0p01_20260314.geojson`)
3. **Actualización**: cron de APScheduler, por defecto `0 3 * * 0` (domingos a las 3 AM), configurable en `settings.json` (`layer.update_cron`)
4. **Caché**: los archivos quedan en `./data/` y las capas cargadas se cachean en memoria (`layer.cache_ttl_minutes`, 30 min); el historial de refrescos se guarda en SQLite

**Capas utilizadas:**

- `ign:pais` - Límites del territorio argentino
- `ign:departamento` - División departamental (todos los departamentos de Argentina)
- `ign:provincia` - División provincial (usada solo para el render de los avisos)

## API Documentation

### Endpoints

| Método | Ruta | Descripción |
| ------ | ---- | ----------- |
| `GET`  | `/` | Estado del servicio |
| `GET`  | `/health` | Health check (lo usa el `healthcheck` de Docker) |
| `POST` | `/intersect/country` | Intersección con el territorio argentino |
| `POST` | `/intersect/departments` | Departamentos que intersecan el polígono |
| `GET`  | `/intersect/layer-refresh-history` | Últimas corridas del refresco de capas (`limit`, 1-100, default 20) |
| `POST` | `/alerts` | Encola la generación de un aviso; devuelve `202` con `job_id` |
| `GET`  | `/alerts` | Avisos vigentes de la tabla externa `taviso` |
| `GET`  | `/alerts/pending` | Avisos pendientes de `taviso_temporal` |
| `GET`  | `/alerts/jobs/{job_id}` | Estado del job: `queued`, `processing`, `done` o `failed` |
| `GET`  | `/alerts/phenomena` | Fenómenos meteorológicos disponibles |
| `GET`  | `/alerts/limits` | Máximo de vértices admitido en el polígono |
| `GET`  | `/metrics/summary` | KPIs de generación de avisos en una ventana de horas |
| `GET`  | `/metrics/jobs` | Jobs terminados recientes |
| `GET`  | `/metrics/jobs/history` | Series de resultados y duración por bucket (`hour` o `day`) |
| `GET`  | `/metrics/processor/history` | Series de cola y workers del procesador |
| `GET`  | `/metrics/layers` | Últimas corridas del refresco de capas |

**Intersección con País**

```bash
POST /intersect/country?detail_level=5
Content-Type: application/json

{
  "type": "FeatureCollection",
  "features": [...]
}
```

**Intersección con Departamentos**

```bash
POST /intersect/departments
Content-Type: application/json

{
  "type": "Feature",
  "geometry": {...}
}
```

### Parámetros de Query

`POST /intersect/country` acepta `detail_level`, un entero entre 1 y 5 (default `5`): a mayor nivel, menor tolerancia de simplificación y más detalle. Las tolerancias por nivel están en la tabla de [Configuration](#configuration).

`POST /intersect/departments` no toma parámetros: siempre usa la capa de departamentos simplificada con `departments_simplify_tolerance` (0.005).

### Formatos de Entrada

Los endpoints aceptan GeoJSON en cualquiera de estos formatos:

- **Geometry**: `{"type": "Polygon", "coordinates": [...]}`
- **Feature**: `{"type": "Feature", "geometry": {...}, "properties": {...}}`
- **FeatureCollection**: `{"type": "FeatureCollection", "features": [...]}`

### Ejemplos de Uso

```bash
# Intersección con país, máximo nivel de detalle expuesto por la API
curl -X POST "http://localhost:6007/intersect/country?detail_level=5" \
  -H "Content-Type: application/json" \
  -d @polygon.json

# Intersección con departamentos
curl -X POST "http://localhost:6007/intersect/departments" \
  -H "Content-Type: application/json" \
  -d '{"type":"Polygon","coordinates":[[[-55.6,-27.1],[-54.8,-26.6],[-53.9,-26.3],[-55.6,-27.1]]]}'
```

### Documentación Interactiva

- **Swagger UI**: <http://localhost:6007/docs>
- **ReDoc**: <http://localhost:6007/redoc>

## Running Tests

```bash
make test
```

Construye la imagen `Dockerfile.run_test` y corre pytest con coverage. Los reportes quedan en `./reports/`.

Los tests se dividen en `tests/unit/` (sin red ni Docker) y `tests/application/` (contra la app FastAPI).

## Makefile Commands

```bash
make up          # Entorno de desarrollo con hot-reload (docker-compose-dev.yaml)
make down        # Detener los contenedores de dev y de producción
make clean       # Detener y eliminar volúmenes
make prod        # Entorno de producción (docker-compose.yaml)
make test        # Tests con pytest y coverage dentro de Docker
make test-api    # Tests de integración contra el servicio corriendo (ver nota abajo)
make precommit   # pre-commit sobre todos los archivos (black, pylint, mypy)
```

`make test-api` ejecuta `tests/test_alerts_api.py`, que no está en el repositorio: hoy el target falla.
