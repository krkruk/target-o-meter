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

# Scratch dir for transient stderr capture (the migrate retry loop).
RUN_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-/tmp}"

echo "▸ migrate"
# podman-compose starts web + worker concurrently; the bind-mount :Z relabel
# (chcon for SELinux) can still be in-flight when the first manage.py call runs,
# surfacing as "Permission denied". Retry a few times — the relabel completes
# within seconds and the retry succeeds. Without this, the loser of the start
# race exits(2) before the relabel finishes.
for attempt in 1 2 3 4 5 6 7 8; do
    if uv run python src/manage.py migrate --noinput 2>"$RUN_DIR/.migrate.err"; then
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

echo "▸ starting $SERVICE_ROLE"
case "${SERVICE_ROLE:-web}" in
    web)
        exec uv run python src/manage.py runserver 0.0.0.0:8000
        ;;
    worker)
        exec uv run python src/manage.py qcluster
        ;;
    *)
        echo "unknown SERVICE_ROLE=${SERVICE_ROLE}; expected 'web' or 'worker'" >&2
        exit 2
        ;;
esac
