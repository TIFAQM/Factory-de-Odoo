#!/usr/bin/env bash
# Lifecycle management script for Odoo 17 CE dev instance
# Usage: scripts/odoo-dev.sh {start|stop|status|reset|logs}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker/dev/docker-compose.yml"
OVERRIDE_FILE="$PROJECT_ROOT/docker/dev/docker-compose.override.yml"
DB_NAME="${ODOO_DEV_DB:-odoo_dev}"
MODULES="base,mail,sale,purchase,hr,account"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

_compose() {
    if [ -f "$OVERRIDE_FILE" ]; then
        docker compose -f "$COMPOSE_FILE" -f "$OVERRIDE_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

_db_exists() {
    _compose exec -T db \
        psql -U odoo -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" \
        2>/dev/null | grep -q 1
}

_init_modules() {
    echo "Initializing database '$DB_NAME' with modules: $MODULES"
    echo "This may take a few minutes on first run..."
    _compose run --rm -T odoo odoo \
        -d "$DB_NAME" \
        -i "$MODULES" \
        --stop-after-init \
        --no-http \
        --log-level=warn
    echo "Module installation complete."
}

_wait_healthy() {
    local elapsed=0
    local timeout=60
    echo -n "Waiting for Odoo to be healthy..."
    while [ "$elapsed" -lt "$timeout" ]; do
        local health
        health=$(_compose ps --format json 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
        if obj.get('Service') == 'odoo' and obj.get('Health') == 'healthy':
            print('healthy')
            sys.exit(0)
    except json.JSONDecodeError:
        continue
sys.exit(1)
" 2>/dev/null) && break
        echo -n "."
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo ""
    if [ "$elapsed" -ge "$timeout" ]; then
        echo "ERROR: Odoo did not become healthy within ${timeout}s"
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_start() {
    echo "Starting Odoo dev instance..."
    _compose up -d --wait db
    if ! _db_exists; then
        _init_modules
    fi
    _compose up -d --wait
    echo "Odoo dev instance ready at http://localhost:${ODOO_DEV_PORT:-8069}"
}

cmd_stop() {
    _compose down
    echo "Odoo dev instance stopped."
}

cmd_status() {
    _compose ps
    if [ -f "$SCRIPT_DIR/verify-odoo-dev.py" ]; then
        echo ""
        echo "--- XML-RPC Connectivity ---"
        python3 "$SCRIPT_DIR/verify-odoo-dev.py" 2>/dev/null || true
    fi
}

cmd_reset() {
    echo "WARNING: This will destroy all dev instance data (database + filestore)."
    _compose down -v
    echo "Dev instance data destroyed. Run '$0 start' to re-initialize."
}

cmd_logs() {
    _compose logs -f "${1:-odoo}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "${1:-help}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    reset)  cmd_reset ;;
    logs)   cmd_logs "${2:-}" ;;
    *)      echo "Usage: $0 {start|stop|status|reset|logs}" ;;
esac
