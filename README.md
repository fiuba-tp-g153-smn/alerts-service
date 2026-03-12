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

- **Geographic Intersection API**: REST endpoints para operaciones de intersección geográfica
  - `POST /intersect-country`: Intersección con territorio argentino
  - `POST /intersect-departments`: Intersección con departamentos provinciales
- **Dual Quality Modes**:
  - **Simplified** (1% tolerance): Respuestas rápidas (~0.3s), calidad excelente para visualización web/móvil
  - **Full Resolution**: Máximo detalle para análisis de precisión (~10s)
- **Automatic Data Management**:
  - Descarga automática de capas geográficas del IGN al inicio
  - Simplificación inteligente de geometrías con tolerancia configurable
  - Caché persistente para optimizar reinicios
- **Production Ready**:
  - Health checks integrados
  - Logging estructurado
  - Documentación automática con Swagger/OpenAPI
  - Actualización mensual automática de datos (cron)
- **Dockerized**: Entorno completamente contenerizado para desarrollo y producción

## Dependencies

Para ejecutar el proyecto no es necesario tener `python` instalado, ya que el proyecto está completamente Dockerizado. Solo es requerido para ejecutar el proyecto de forma nativa.

Dependencias necesarias:

- **Docker**: para ejecutar el proyecto en un entorno contenerizado
- **Make**: para simplificar y automatizar comandos
- **Python v3.10+**: solo si decides ejecutar la aplicación de forma nativa (sin Docker)

## Setup for Development

1. Clona el repositorio:

   ```bash
   git clone https://github.com/fiuba-tp-g153-smn/mapasmn.git
   cd mapasmn/alerts-service
   ```

2. Copia el archivo de configuración de ejemplo:

   ```bash
   cp .env.example .env
   ```

   Edita `.env` para configurar variables de entorno (tolerancia de simplificación, puerto, logs).

3. Para desarrollo local:

   **Con Docker (recomendado):**

   ```bash
   make dev
   ```

   La aplicación estará disponible en <http://localhost:8080>

   **Sin Docker:**

   ```bash
   # Crear entorno virtual
   python -m venv .venv
   source .venv/bin/activate  # En Windows: .venv\Scripts\activate
   
   # Instalar dependencias
   make install
   
   # Ejecutar servicio
   make local
   ```

   La aplicación estará disponible en <http://localhost:8080>

## Configuration

### Variables de Entorno

Edita `.env` o configura variables de entorno:

```bash
# Puerto del servicio
APP_HOST_PORT=8080

# Entorno de ejecución
APP_ENV=development

# Nivel de logging
LOG_LEVEL=INFO

# Tolerancia de simplificación de geometrías (0.001 - 0.1)
# 0.001 (0.1%): Máximo detalle, más lento
# 0.01  (1%):   Excelente calidad, buen rendimiento ⭐ (default)
# 0.05  (5%):   Buena calidad, más rápido
# 0.1   (10%):  Menor detalle, máxima velocidad
SIMPLIFY_TOLERANCE=0.01
```

### Geographic Data

El servicio gestiona automáticamente los datos geográficos:

1. **Al iniciar**: Descarga capas del IGN si no existen localmente
2. **Simplificación**: Genera versiones optimizadas según `SIMPLIFY_TOLERANCE`
3. **Actualización**: Cron job mensual (día 1 a las 3 AM) actualiza los datos
4. **Caché**: Almacena en `./data/` para optimizar reinicios

**Capas utilizadas:**

- `ign:pais` - Límites del territorio argentino
- `ign:departamento` - División departamental (todos los departamentos de Argentina)

**Archivos generados:**

- `pais.geojson` (108 MB) → `pais_simple.geojson` (554 KB)
- `departamentos.geojson` (134 MB) → `departamentos_simple.geojson` (1.2 MB)

## API Documentation

### Endpoints Principales

**Health Check**

```bash
GET /health
```

**Intersección con País**

```bash
POST /intersect-country?use_simplified=true
Content-Type: application/json

{
  "type": "FeatureCollection",
  "features": [...]
}
```

**Intersección con Departamentos**

```bash
POST /intersect-departments?use_simplified=true
Content-Type: application/json

{
  "type": "Feature",
  "geometry": {...}
}
```

### Parámetros de Query

Ambos endpoints aceptan el parámetro `use_simplified`:

- `true` (default): Usa geometrías simplificadas (~0.3s, 25-56x más rápido)
- `false`: Usa geometrías completas (~10s, máximo detalle)

### Formatos de Entrada

Los endpoints aceptan GeoJSON en cualquiera de estos formatos:

- **Geometry**: `{"type": "Polygon", "coordinates": [...]}`
- **Feature**: `{"type": "Feature", "geometry": {...}, "properties": {...}}`
- **FeatureCollection**: `{"type": "FeatureCollection", "features": [...]}`

### Ejemplos de Uso

```bash
# Intersección con país (simplificada)
curl -X POST "http://localhost:8080/intersect-country?use_simplified=true" \
  -H "Content-Type: application/json" \
  -d @polygon.json

# Intersección con departamentos (completa)
curl -X POST "http://localhost:8080/intersect-departments?use_simplified=false" \
  -H "Content-Type: application/json" \
  -d '{"type":"Polygon","coordinates":[[[-55.6,-27.1],[-54.8,-26.6],[-53.9,-26.3],[-55.6,-27.1]]]}'
```

### Documentación Interactiva

- **Swagger UI**: <http://localhost:8080/docs>
- **ReDoc**: <http://localhost:8080/redoc>

## Running Tests

### Unit Tests

```bash
make test
```

Ejecuta pytest con coverage. Resultados en `./reports/`.

### API Integration Tests

```bash
# Prerequisito: servicio debe estar corriendo
make dev

# En otra terminal, ejecutar tests de integración
make test-api
```

Los tests de integración:

- Prueban ambos endpoints con versiones simplificada y completa
- Miden tiempos de respuesta
- Generan archivos GeoJSON de resultado en `tests/`

**Archivos generados:**

- `tests/country_simplified.json` - Intersección con país (simplificado)
- `tests/country_full.json` - Intersección con país (completo)
- `tests/departments_simplified.geojson` - Departamentos (simplificado) ⭐
- `tests/departments_full.geojson` - Departamentos (completo) ⭐

Ver [tests/README.md](tests/README.md) para más detalles.

## Makefile Commands

```bash
# Development
make install          # Instalar dependencias con Poetry
make dev              # Iniciar en modo desarrollo (con logs)
make dev-detached     # Iniciar en background
make logs             # Ver logs del contenedor
make stop             # Detener contenedor
make clean            # Detener y eliminar volúmenes

# Production
make prod             # Iniciar en modo producción
make prod-stop        # Detener producción

# Local (sin Docker)
make local            # Ejecutar con uvicorn localmente

# Testing
make test             # Tests unitarios con pytest
make test-api         # Tests de integración API
```
