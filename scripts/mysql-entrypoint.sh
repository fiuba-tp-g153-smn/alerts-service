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

# External taviso database provisioning (dev/test only)
# =====================================================
# In production the external `taviso` table lives in the client's database and is
# accessed read-only — we never provision it. When MANAGE_TAVISO_SCHEMA is enabled
# (dev/test) we simulate that external database as a separate schema on this same
# server: create the database, a dedicated read-only user, and grant the app user
# (${MYSQL_USER}) DDL on it so Alembic migration 005 can create the `taviso` table.
case "${MANAGE_TAVISO_SCHEMA,,}" in
  1 | true | yes)
    cat >> /tmp/grants.sql <<EOF

CREATE DATABASE IF NOT EXISTS \`${MYSQL_TAVISO_DATABASE}\` CHARACTER SET latin1;

-- Dedicated external read-only user for the taviso database
CREATE USER IF NOT EXISTS '${MYSQL_TAVISO_USER}'@'%' IDENTIFIED BY '${MYSQL_TAVISO_PASSWORD}';
GRANT SELECT ON \`${MYSQL_TAVISO_DATABASE}\`.* TO '${MYSQL_TAVISO_USER}'@'%';

-- App user needs DDL on the taviso schema so Alembic can create the table there
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES ON \`${MYSQL_TAVISO_DATABASE}\`.* TO '${MYSQL_USER}'@'%';

FLUSH PRIVILEGES;
EOF
    ;;
esac

# Unset vars consumed by the upstream docker-entrypoint.sh: when MYSQL_USER/PASSWORD
# are set, it runs a non-idempotent `CREATE USER` after our --init-file has already
# created the same user, which fails with ERROR 1396. MYSQL_DATABASE is similarly
# redundant since our grants file creates it. Root password is still needed.
unset MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD

exec docker-entrypoint.sh mysqld --init-file=/tmp/grants.sql "$@"
