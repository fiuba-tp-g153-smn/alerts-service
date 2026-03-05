#!/bin/bash
set -e

# Create log file for cron
touch /var/log/cron.log

# Start cron in the background
cron

# Run geo preprocessing on startup
if [ -f /app/scripts/download_and_simplify_layers.py ]; then
    echo "Running initial geo preprocessing..."
    python3 /app/scripts/download_and_simplify_layers.py
fi

# Start the main application
exec "$@"
