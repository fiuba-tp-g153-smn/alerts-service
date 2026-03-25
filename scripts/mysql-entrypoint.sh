#!/bin/bash
set -e

# Write a grants file using current env vars.
# --init-file runs this SQL on every MySQL startup (as root, no auth needed),
# ensuring the app user always has % host access regardless of volume state.
cat > /tmp/grants.sql <<EOF
CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;
EOF

exec docker-entrypoint.sh mysqld --init-file=/tmp/grants.sql "$@"
