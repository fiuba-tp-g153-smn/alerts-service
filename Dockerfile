################################
# Stage 1: Builder
################################
FROM python:3.13.12-slim-trixie AS builder

WORKDIR /app

# Copy only dependency manifests first (to leverage Docker build cache for faster builds)
COPY pyproject.toml poetry.lock /app/

# Install Poetry, disable venvs to install into system site-packages
RUN pip install --no-cache-dir "poetry" && poetry config virtualenvs.create false

# Re-generate lock file if it is outdated, then install all dependencies (except dev/test deps)
RUN (poetry check --lock || poetry lock) && poetry install --without dev --no-root --no-ansi

################################
# Stage 2: Runtime
################################
FROM python:3.13.12-slim-trixie AS runner

WORKDIR /app

# Use python implementation of protobuf instead of binary
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the actual application code into /app
COPY ./src /app

# Copy settings file
COPY settings.json /config/settings.json

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
