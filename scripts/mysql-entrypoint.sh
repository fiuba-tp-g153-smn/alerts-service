#!/bin/bash
set -e

# MySQL User Provisioning
# =======================
# This script generates a grants SQL file and passes it to MySQL via --init-file,
# which runs on every MySQL startup as root (no auth needed). This ensures users
# and privileges are always in sync with environment variables, regardless of
# whether the data volume already exists.
#
# Users created:
#
#   root (built-in)
#     - Created automatically by the MySQL Docker image
#     - Restricted to localhost via MYSQL_ROOT_HOST=localhost in docker-compose
#     - Used only by this init script and for manual DB administration
#     - NOT used by the application
#
#   ${MYSQL_USER} (e.g. alerts_service)
#     - Internal app user, used by FastAPI via connection pool and by Alembic migrations
#     - DML privileges: SELECT, INSERT, UPDATE, DELETE (app operations)
#     - DDL privileges: CREATE, ALTER, DROP, INDEX, REFERENCES (Alembic migrations)
#     - No administrative privileges (GRANT, SUPER, EVENT, TRIGGER, etc.)
#
#   ${MYSQL_READONLY_USER} (e.g. avisos)
#     - External readonly user for BI tools and other services connecting to port 3306
#     - SELECT only, rate-limited to prevent connection abuse
#     - NOT used by the application itself
#
# To apply changes: docker compose down -v && docker compose up
# (grants are baked into the MySQL data directory on first init)

cat > /tmp/grants.sql <<EOF
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- App user: DML + DDL for normal operations and Alembic migrations
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';

-- External readonly user: SELECT only, rate-limited
CREATE USER IF NOT EXISTS '${MYSQL_READONLY_USER}'@'%' IDENTIFIED BY '${MYSQL_READONLY_PASSWORD}';
GRANT SELECT ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_READONLY_USER}'@'%';
ALTER USER '${MYSQL_READONLY_USER}'@'%' WITH MAX_USER_CONNECTIONS ${MYSQL_READONLY_MAX_CONNECTIONS} MAX_CONNECTIONS_PER_HOUR ${MYSQL_READONLY_MAX_CONNECTIONS_PER_HOUR};

FLUSH PRIVILEGES;
EOF

exec docker-entrypoint.sh mysqld --init-file=/tmp/grants.sql "$@"
