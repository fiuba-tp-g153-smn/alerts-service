#!/bin/bash
set -e

echo "INFO [entrypoint] Running Alembic migrations..."
if alembic upgrade head; then
    echo "INFO [entrypoint] Alembic migrations completed successfully."
else
    echo "ERROR [entrypoint] Alembic migrations failed. Aborting startup." >&2
    exit 1
fi

exec "$@"
