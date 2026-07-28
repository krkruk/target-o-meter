#!/usr/bin/env bash
# Idempotent dev seed — safe to re-run on every ``docker compose up``.
#
# Runs ``migrate`` unconditionally, then seeds three rows via the app's own
# service surface (AGENTS.md §5 — no ORM tools against domain models):
#   - a dev admin (Django admin login; usable password)
#   - the Owner row (sub = $OWNER_SUB_ID; role is *derived* on read, so the
#     seed only creates the row — moving $OWNER_SUB_ID re-roles it)
#   - a plain User row for role testing
#
# All three are idempotent (``get_or_create``-shaped). Then it execs the
# role-specific server command ($SERVICE_ROLE: ``web`` → runserver, ``worker``
# → qcluster), becoming PID 1 so signals reach Django directly.
set -euo pipefail

# Scratch dir for transient stderr capture (the migrate retry loop) + the
# post-migrate sentinel (web writes it after migrate+seed; worker's
# depends_on: web: service_healthy probes it via the web healthcheck).
RUN_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-/tmp}"
SENTINEL="$RUN_DIR/.dev-seed-complete"

case "${SERVICE_ROLE:-web}" in
    web)
        # Wipe any stale sentinel from a prior boot so it can't falsely
        # advertise migrate-done while this boot's migrate is still running.
        rm -f "$SENTINEL"
        ;;
    worker)
        # The worker does NOT migrate or seed — only web does. Running migrate
        # here too races web's migrate against the SAME shared SQLite volume
        # (the ``duplicate column name`` boot crash: both did ALTER TABLE ADD
        # COLUMN concurrently on vision.0004). worker's depends_on waits on
        # web's healthcheck (the sentinel = migrate done), so the schema is
        # migrated before qcluster boots.
        echo "▸ starting worker (waiting on web migrate via depends_on healthcheck)"
        exec uv run python src/manage.py qcluster
        ;;
    *)
        echo "unknown SERVICE_ROLE=${SERVICE_ROLE}; expected 'web' or 'worker'" >&2
        exit 2
        ;;
esac

echo "▸ migrate"
# podman-compose starts web + worker concurrently; the bind-mount :Z relabel
# (chcon for SELinux) can still be in-flight when the first manage.py call runs,
# surfacing as "Permission denied". Retry a few times — the relabel completes
# within seconds and the retry succeeds. Without this, the loser of the start
# race exits(2) before the relabel finishes.
migrated=0
for attempt in 1 2 3 4 5 6 7 8; do
    if uv run python src/manage.py migrate --noinput 2>"$RUN_DIR/.migrate.err"; then
        migrated=1
        break
    fi
    if grep -q "Permission denied" "$RUN_DIR/.migrate.err" 2>/dev/null; then
        echo "  migrate: Permission denied (SELinux relabel in-flight?), retry $attempt/8..."
        sleep 2
        continue
    fi
    cat "$RUN_DIR/.migrate.err" >&2
    exit 1
done
# If all 8 attempts hit "Permission denied" the loop falls through here
# without ever `break`ing — fail loudly rather than seeding an unmigrated DB.
if [ "$migrated" -ne 1 ]; then
    echo "  migrate: Permission denied persisted across all 8 attempts" >&2
    cat "$RUN_DIR/.migrate.err" >&2
    exit 1
fi

echo "▸ dev seed (admin + owner + user)"
# ``manage.py shell``-invoked seed using the identity domain's service surface.
# Idempotent: re-runs are no-ops once the rows exist.
uv run python src/manage.py shell -c "
import os
from src.domains.identity.models import User
from src.domains.identity.services import get_or_create_user_by_sub

# Dev admin (Django admin login). Only create if absent — re-runs skip it.
admin_sub = os.environ.get('DEV_ADMIN_SUB', '')
admin_nick = os.environ.get('DEV_ADMIN_NICK', 'dev-admin')
admin_pw = os.environ.get('DEV_ADMIN_PASSWORD', '')
if admin_sub and admin_pw and not User.objects.filter(sub=admin_sub).exists():
    User.objects.create_superuser(sub=admin_sub, nick=admin_nick, password=admin_pw)
    print(f'  seeded dev admin: {admin_nick} (sub={admin_sub})')

# Owner row. Role is derived from OWNER_SUB_ID on read, so the seed only
# creates the row; moving OWNER_SUB_ID re-roles it without a DB write.
owner_sub = os.environ.get('OWNER_SUB_ID', '')
if owner_sub:
    dto = get_or_create_user_by_sub(owner_sub)
    print(f'  ensured owner row for sub={owner_sub}')

# Plain dev user (for role testing).
get_or_create_user_by_sub('dev-user-sub')
print('  ensured dev-user row')
"

# Signal migrate+seed done so the worker's healthcheck gate passes. Written
# AFTER the seed and BEFORE exec'ing the role command, so "healthy" means the
# schema is migrated (exactly what the worker needs before booting qcluster).
touch "$SENTINEL"

echo "▸ starting $SERVICE_ROLE"
case "${SERVICE_ROLE:-web}" in
    web)
        exec uv run python src/manage.py runserver 0.0.0.0:8000
        ;;
    worker)
        exec uv run python src/manage.py qcluster
        ;;
esac
