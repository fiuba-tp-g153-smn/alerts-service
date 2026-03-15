################################
# Stage 1: Builder
################################
FROM python:3.13.12-slim-trixie AS builder

WORKDIR /app

# Install system dependencies for matplotlib/cartopy
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    libgeos-dev libproj-dev proj-data proj-bin \
    libgdal-dev gdal-bin \
    libspatialindex-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency manifests first (to leverage Docker build cache for faster builds)
COPY pyproject.toml poetry.lock /app/

# Install Poetry, disable venvs to install into system site-packages
RUN pip install --no-cache-dir "poetry" && poetry config virtualenvs.create false

# Re-generate lock file if it is outdated, then install all dependencies (except dev/test deps)
RUN (poetry check --lock || poetry lock) && poetry install --without dev --no-root --no-ansi

# Pre-download Natural Earth 50m data during build (saves ~100MB download at runtime)
RUN python -c "import cartopy.io.shapereader as sr; \
    combos = [('physical','coastline'),('physical','land'),('physical','ocean'),\
              ('cultural','admin_0_boundary_lines_land')]; \
    [list(sr.Reader(sr.natural_earth('50m',c,n)).geometries()) for c,n in combos]; \
    print('Natural Earth 50m cached')"

################################
# Stage 2: Runtime
################################
FROM python:3.13.12-slim-trixie AS runner

WORKDIR /app

# Install runtime dependencies for matplotlib/cartopy
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgeos-c1v5 libproj25 \
    libgdal35 \
    libspatialindex-c6 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Use python implementation of protobuf instead of binary
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy Natural Earth cache from builder
COPY --from=builder /root/.local/share/cartopy /root/.local/share/cartopy

# Create output directories
RUN mkdir -p /app/output/alerts /app/cache

# Copy the actual application code into /app
COPY ./src /app

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
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=5m --start-interval=10s CMD python -c 'import urllib.request; urllib.request.urlopen("http://localhost:8080/health")'

CMD ["uvicorn", "main:app", "--host=0.0.0.0", "--port", "8080"]
