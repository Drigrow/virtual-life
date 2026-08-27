#!/usr/bin/env bash
#
# Virtual Life — One-click Safe Update Script
#
# Workflow:
#   1) Archives & backs up all existing conversation history, memory, persona,
#      images, state, and .env into a timestamped snapshot before touching code.
#   2) Pulls the latest code from GitHub (git pull).
#   3) Updates Python virtualenv dependencies (pip install -r requirements.txt).
#   4) Gracefully restarts the systemd service (if installed) and verifies health.
#
# Usage:
#   bash scripts/update.sh          # from repo root: bash scripts/update.sh
#   bash update.sh                  # or directly via root shortcut
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${APP_DIR}/backups"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
SNAPSHOT_NAME="snapshot_${TIMESTAMP}"
SNAPSHOT_PATH="${BACKUP_DIR}/${SNAPSHOT_NAME}.tar.gz"

say() { printf '\033[1;36m>>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

say "=========================================="
say "  Virtual Life — Safe Update & Archive   "
say "=========================================="

# ── Step 1: Pre-update Archive & Backup ──────────────────────────────────────
say "1/4 Creating pre-update backup snapshot..."
mkdir -p "$BACKUP_DIR"

# Collect all runtime data files to archive
FILES_TO_BACKUP=()
shopt -s nullglob
for f in "$APP_DIR"/*.md "$APP_DIR"/*.json "$APP_DIR"/.env; do
  [ -f "$f" ] && FILES_TO_BACKUP+=("$(basename "$f")")
done
[ -d "$APP_DIR/chat_images" ] && FILES_TO_BACKUP+=("chat_images")
shopt -u nullglob

if [ ${#FILES_TO_BACKUP[@]} -gt 0 ]; then
  tar -czf "$SNAPSHOT_PATH" -C "$APP_DIR" "${FILES_TO_BACKUP[@]}" 2>/dev/null || true
  say "Snapshot saved: ${SNAPSHOT_PATH} ($(du -sh "$SNAPSHOT_PATH" 2>/dev/null | cut -f1 || echo 'ok'))"
else
  say "No existing data files found to archive."
fi

# Also trigger routine backup script if configured
if [ -f "$SCRIPT_DIR/backup.sh" ] && [ -f "${APP_DIR}-data/backup.conf" ]; then
  say "Triggering incremental routine backup..."
  bash "$SCRIPT_DIR/backup.sh" run || true
fi

# ── Step 2: Code Update (Git Pull) ──────────────────────────────────────────
say "2/4 Updating codebase from Git..."
if [ -d "$APP_DIR/.git" ]; then
  CURRENT_BRANCH="$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'main')"
  say "Current branch: $CURRENT_BRANCH"

  # Fetch and pull
  git -C "$APP_DIR" fetch --quiet origin "$CURRENT_BRANCH" || warn "Fetch failed, trying pull directly..."
  
  if ! git -C "$APP_DIR" pull --ff-only origin "$CURRENT_BRANCH"; then
    warn "Fast-forward pull failed. Attempting standard git pull..."
    git -C "$APP_DIR" pull origin "$CURRENT_BRANCH" || die "Git pull failed. Please check local git status."
  fi
  say "Code updated to: $(git -C "$APP_DIR" log -1 --oneline)"
else
  warn "Not a git repository directory. Skipping git pull."
fi

# ── Step 3: Dependencies Update ─────────────────────────────────────────────
say "3/4 Checking and updating dependencies..."
VENV="$APP_DIR/.venv"
if [ -x "$VENV/bin/pip" ]; then
  say "Updating dependencies using $VENV/bin/pip..."
  "$VENV/bin/pip" install --quiet --upgrade -r "$APP_DIR/requirements.txt"
elif command -v uv >/dev/null 2>&1 && [ -x "$VENV/bin/python" ]; then
  say "Updating dependencies using uv..."
  uv pip install --quiet --python "$VENV/bin/python" -r "$APP_DIR/requirements.txt"
else
  warn "No .venv/bin/pip found. If needed, run 'python setup.py' or 'bash scripts/deploy.sh'."
fi

# ── Step 4: Service Restart & Health Check ───────────────────────────────────
say "4/4 Checking running services and restarting..."

# Search for any systemd unit pointing to this directory
SERVICE_NAME=""
for svc in virtual-life virtual-life-2 virtual-life-3; do
  unit_file="/etc/systemd/system/${svc}.service"
  if [ -f "$unit_file" ] && grep -q "WorkingDirectory=$APP_DIR" "$unit_file"; then
    SERVICE_NAME="$svc"
    break
  fi
done

if [ -n "$SERVICE_NAME" ] && command -v systemctl >/dev/null 2>&1; then
  if [ "$(id -u)" -eq 0 ]; then
    say "Reloading and restarting systemd service: $SERVICE_NAME..."
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
      say "Service $SERVICE_NAME is ACTIVE and healthy!"
      systemctl status "$SERVICE_NAME" --no-pager | head -10 || true
    else
      warn "Service $SERVICE_NAME failed to restart properly. Check: journalctl -u $SERVICE_NAME -n 30"
    fi
  else
    warn "Non-root user: please restart the service manually with: sudo systemctl restart $SERVICE_NAME"
  fi
else
  say "No systemd service found for this directory. If running manually, please restart 'python main.py'."
fi

say "=========================================="
say "  Update Complete!                        "
say "  Backup Archive: $SNAPSHOT_PATH"
say "=========================================="
