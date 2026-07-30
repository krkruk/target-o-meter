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

## API endpoint URIs name resources, not actions

- **Context**: REST API endpoint design across the BFF layer (`src/bff/routers/`), whenever a new route is added or named.
- **Problem**: Verb-in-URI naming (e.g. `/v1/scoring/aggregate`, `/v1/process-image`) reads as an RPC call, conflicts with REST resource semantics, and produces inconsistent endpoint shapes — some noun-named, some verb-named — that are hard to discover and reason about. It also leaks the operation into the path rather than letting the HTTP method carry it.
- **Rule**: API endpoint URIs name resources, not actions — always use plural nouns and no verbs. The HTTP method carries the verb (GET to read, POST to create, etc.). Example: `GET /v1/scores/aggregations` (correct, resource-oriented) not `GET /v1/scoring/aggregate` (wrong, verb in URI). When adding a BFF route, name the path after the resource being accessed.
- **Applies to**: plan, implement, impl-review

## Enable faulthandler so a native crash prints a traceback, not silence

- **Context**: Any pipeline that calls into native C extensions from a worker process — here, the vision domain's opencv (`cv2`)/numpy geometry step running inside a django-q2 worker. Especially relevant in constrained/production environments (Railway, Docker) where the worker is a subprocess and you read logs after the fact.
- **Problem**: When a native C extension crashes (segfault / SIGSEGV / SIGFPE / SIGABRT), the OS kills the process *before* Python's exception machinery runs. So a `try/except Exception` around the call (the natural instinct) **cannot catch it** — `except` never executes, `logger.exception(...)` never fires, and the only signal is the queue framework's generic "worker died / reincarnated worker after death" line. The crash is completely silent: no traceback, no stage, no exception type. This wastes hours chasing memory, credentials, or code bugs that aren't the cause (the vision worker-death investigation burned two wrong hypotheses — OOM and stale creds — before the silent-crash class was recognized).
- **Rule**: For any worker/native-C path, enable `faulthandler` in the Django `AppConfig.ready()` (so it covers gunicorn, qcluster, and forked worker subprocesses): `faulthandler.enable()` installs a signal handler that dumps the current Python stack to stderr on a fatal native signal, turning a silent segfault into a locatable traceback. Pair it with stage INFO logging at each native-call boundary (e.g. "geometry done", "llm_input written", "detect done") so a future death localizes to the exact stage. Cost is one installed handler with no per-request overhead; the value is that the next native crash names its location instead of restarting the investigation from zero.
- **Applies to**: plan, implement
