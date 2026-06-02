#!/bin/bash
set -e

# MySQL User Provisioning
# =======================
# This script generates a grants SQL file and passes it to MySQL via --init-file,
# which runs on every MySQL startup as root (no auth needed). This ensures users
# and privileges are always in sync, regardless of whether the data volume already
# exists. It depends only on the base MYSQL_* vars (always present) — no taviso vars.
#
# Users created:
#
#   root (built-in)
#     - Created automatically by the MySQL Docker image, restricted to localhost.
#     - Used only by this init script and for manual DB administration.
#
#   ${MYSQL_USER} (e.g. alerts_service)
#     - Internal app user, used by FastAPI and by Alembic migrations.
#     - DML + DDL on the app database.
#     - TRIGGER + routine privileges so Alembic migration 006 can create the
#       dev/test taviso sync stored procedure and trigger. In real production the
#       migration is gated off (MANAGE_TAVISO_SCHEMA unset) so these go unused.
#
#   ${MYSQL_READONLY_USER} (e.g. avisos)
#     - External readonly user (SELECT only, rate-limited).
#     - Also used by the app's read-only connection to the `taviso` table, which in
#       dev/test lives in this same database (MYSQL_TAVISO_DATABASE=${MYSQL_DATABASE}).
#
# To apply changes: recreate the container (down/up or --force-recreate). The
# --init-file re-runs on every startup, so a restart re-applies the grants.

cat > /tmp/grants.sql <<EOF
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Allow the non-SUPER app user to create the simulated taviso sync trigger/procedure
-- (Alembic migration 006) while binary logging is enabled. Re-applied each startup.
SET GLOBAL log_bin_trust_function_creators = 1;

-- App user: DML + DDL for normal operations and Alembic migrations, plus TRIGGER and
-- routine privileges for the dev/test taviso sync simulation (migration 006).
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES, TRIGGER, CREATE ROUTINE, ALTER ROUTINE, EXECUTE ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';

-- External readonly user: SELECT only, rate-limited. Also serves the app's read-only
-- connection to the \`taviso\` table (same database in dev/test).
CREATE USER IF NOT EXISTS '${MYSQL_READONLY_USER}'@'%' IDENTIFIED BY '${MYSQL_READONLY_PASSWORD}';
GRANT SELECT ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_READONLY_USER}'@'%';
ALTER USER '${MYSQL_READONLY_USER}'@'%' WITH MAX_USER_CONNECTIONS ${MYSQL_READONLY_MAX_CONNECTIONS} MAX_CONNECTIONS_PER_HOUR ${MYSQL_READONLY_MAX_CONNECTIONS_PER_HOUR};

FLUSH PRIVILEGES;
EOF

# Unset vars consumed by the upstream docker-entrypoint.sh: when MYSQL_USER/PASSWORD
# are set, it runs a non-idempotent `CREATE USER` after our --init-file has already
# created the same user, which fails with ERROR 1396. MYSQL_DATABASE is similarly
# redundant since our grants file creates it. Root password is still needed.
unset MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD

exec docker-entrypoint.sh mysqld --init-file=/tmp/grants.sql "$@"
