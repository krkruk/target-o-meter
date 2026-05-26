# Lessons Learned

> Append-only register of recurring rules and patterns. Re-read at start by /10x-frame, /10x-research, /10x-plan, /10x-plan-review, /10x-implement, /10x-impl-review.

## Always set RAILPACK_DJANGO_APP_NAME to the full WSGI module path

- **Context**: Any deployment of a Django project to Railway using Railpack, especially when the Django package name contains underscores (e.g. `target_o_meter`).
- **Problem**: Railpack's Django detection constructs the gunicorn start command as `gunicorn {appName}:application`. When `RAILPACK_DJANGO_APP_NAME` is set to just the package name (e.g. `target_o_meter`), gunicorn looks for `application` in the package `__init__.py` instead of `wsgi.py`, causing `Failed to find attribute 'application'` and a crash-loop.
- **Rule**: Always set `RAILPACK_DJANGO_APP_NAME` to the full WSGI module path (e.g. `myapp.wsgi`), never just the Django package name. Railpack appends `:application` to whatever value you provide.
- **Applies to**: plan, implement
