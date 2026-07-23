# Lessons Learned

> Append-only register of recurring rules and patterns. Re-read at start by /10x-frame, /10x-research, /10x-plan, /10x-plan-review, /10x-implement, /10x-impl-review.

## Always set RAILPACK_DJANGO_APP_NAME to the full WSGI module path

- **Context**: Any deployment of a Django project to Railway using Railpack, especially when the Django package name contains underscores (e.g. `target_o_meter`).
- **Problem**: Railpack's Django detection constructs the gunicorn start command as `gunicorn {appName}:application`. When `RAILPACK_DJANGO_APP_NAME` is set to just the package name (e.g. `target_o_meter`), gunicorn looks for `application` in the package `__init__.py` instead of `wsgi.py`, causing `Failed to find attribute 'application'` and a crash-loop.
- **Rule**: Always set `RAILPACK_DJANGO_APP_NAME` to the full WSGI module path (e.g. `myapp.wsgi`), never just the Django package name. Railpack appends `:application` to whatever value you provide.
- **Applies to**: plan, implement

## One class per file, matching filename

- **Context**: Python code generation / OOP restructuring, especially when porting procedural code (functions + helpers) into a DDD domain under `src/domains/<domain>/`.
- **Problem**: When a module accumulates multiple classes (or a class + unrelated helpers), files grow into grab-bags, names stop matching paths, and agents regenerate duplicate classes in adjacent files because "the class looked like it belonged there too."
- **Rule**: One class per file, and the filename matches the class name in snake_case (`GoogleAIStudioDetector` → `google_ai_studio_detector.py`). A file may hold supporting module-level constants and private helpers that serve *only* that class, but no second class. Pure contract collections are the explicit exception — `ports.py` (Protocol/ABC interfaces) and `dtos.py` (Pydantic DTOs) may hold several contracts each, because they ARE the domain's typed boundary, not implementation modules.
- **Applies to**: plan, implement
