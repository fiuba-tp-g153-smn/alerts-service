################################
# Stage 1: Builder
################################
FROM python:3.13.13-slim-trixie AS builder

WORKDIR /app

# Install system dependencies for matplotlib/cartopy/cairosvg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    libgeos-dev libproj-dev proj-data proj-bin \
    libgdal-dev gdal-bin \
    libspatialindex-dev \
    libcairo2 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency manifests first (to leverage Docker build cache for faster builds)
COPY pyproject.toml poetry.lock /app/

# Install Poetry, disable venvs to install into system site-packages
RUN pip install --no-cache-dir "poetry==2.3.2" && poetry config virtualenvs.create false

# Re-generate lock file if it is outdated, then install all dependencies (except dev/test deps)
RUN (poetry check --lock || poetry lock) && poetry install --without dev --no-root --no-ansi --no-cache

################################
# Stage 2: Runtime
################################
FROM python:3.13.13-slim-trixie AS runner

WORKDIR /app

# Install runtime dependencies for matplotlib/cartopy/cairosvg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgeos-c1t64 libproj25 \
    libgdal36 \
    libspatialindex-c8 \
    libcairo2 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Use python implementation of protobuf instead of binary
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create output directories
RUN mkdir -p /app/output/alerts /app/cache

# Copy the actual application code into /app
COPY ./src /app
COPY alembic.ini /config/alembic.ini
COPY alembic /app/alembic

# Copy settings file
COPY settings.json /config/settings.json

# Copy font, logo assets and IGN shapefiles
COPY ./data_alerts /app/data_alerts

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose port 8080 (FastAPI main.py sets default to 8080)
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]

# Run the app with uvicorn
# - "main:app" : entrypoint -> file main.py, ASGI app instance "app"
# - host=0.0.0.0 : bind to all network interfaces (needed in containers)
# - port=8080 : matches EXPOSE above
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=8m --start-interval=10s CMD python -c 'import urllib.request; urllib.request.urlopen("http://localhost:8080/health")'

CMD ["uvicorn", "main:app", "--host=0.0.0.0", "--port", "8080"]
