# Failure playbook (reference)

Load this when a system test FAILS. It covers the log-grep vocabulary for spotting runtime errors, the hypothesis checklist, and a worked end-to-end example (the dev-bypass nick-collision bug) showing the unit/integration-first debug loop.

## The core rule: reproduce unit/integration-first, NOT in the system test

A failing system test tells you **the assembled system is broken**, but isolates the cause poorly — everything runs in one server process, the run takes seconds-to-tens-of-seconds, and the failing assertion is far from the faulty code. Your first move is to **reproduce the failure at the cheapest tier that can express it**, which is almost always a unit or integration test.

```
1. READ        → read the captured logs + the DB file; form a one-sentence hypothesis.
2. REPRODUCE   → write a FAILING unit/integration test that reproduces the hypothesis (red).
                 The Django test client is fine here — fast, isolated, ORM-accessible.
3. FIX         → minimal production change to turn the repro test green.
4. RE-RUN      → re-run the unit/integration suite; confirm green.
5. SYSTEM TEST → re-run the SYSTEM TEST in a FRESH results/<run-id>/ (clean DB, new process).
                 The unit repro proves the cause; only the system test proves the fix
                 holds end-to-end.
6. LAND BOTH   → repro test = permanent regression guard; system test = blackbox contract.
```

**Why not debug directly in the system test?** Because (a) each system-test run is slow, (b) the system test can't easily reach into the ORM/internals to set up the precise failing condition, and (c) a fix verified only by the system test leaves no fast regression guard behind. The unit repro gives you a 50ms feedback loop and a permanent test; the system test gives you the end-to-end proof. You need both.

**Exception:** if you literally cannot express the failure at unit/integration level (e.g. it's a WSGI middleware ordering bug only triggered by the real serving stack), then and only then reproduce it in a blackbox system test (like `test_dev_bypass_colliding_sub_does_not_500`). That test is slower but still becomes a permanent regression guard.

## Step 1 — Read the logs and form a hypothesis

Start with the captured stderr at `results/<run-id>/runserver.stderr`. Scan for these markers (vocabulary that maps a log line to a class of failure):

| Log marker | What it means | Likely tier to repro |
|---|---|---|
| `Traceback (most recent call last)` | An unhandled exception → 500 in DEBUG. The single most important signal. | unit/integration (the exception names the faulty function) |
| `IntegrityError` / `UNIQUE constraint failed` / `<index>` | A DB constraint violation on INSERT/UPDATE — usually a uniqueness or FK bug. | unit/integration (construct the colliding rows directly) |
| `DoesNotExist` / `ObjectDoesNotExist` | A lookup that assumed a row exists. Often a missing seed or a race. | unit/integration |
| `KeyError` / `TypeError: ... NoneType` | A payload-shape mismatch — the app read a field the request didn't send. | integration (send the malformed payload via test client) |
| `PermissionDenied` / `HttpError(403)` | An authz check fired — could be correct (assert 403) or a wiring bug (assert 200). | integration |
| `ConnectionError` / `EADDRINUSE` / `ECONNREFUSED` | Harness/infra problem, NOT a product bug. Check the port, the readiness poll, the process teardown. | fix the harness, not the app |
| `RuntimeWarning` / `DeprecationWarning` | Not a failure by itself, but often precedes a real bug. Note it; don't chase it unless the test fails. | — |

**Form a one-sentence hypothesis** before writing any code. Bad: "the login is broken." Good: "`DevAuthBypassMiddleware` derives the nick as `dev-<sub[:8]>`; two subs sharing the `auth0|` prefix produce the same 8-char slice, and `get_or_create`'s INSERT collides on `identity_user_nick_ci_unique`." A precise hypothesis dictates a precise repro.

If the log is empty or unhelpful, **read the DB file** (`results/<run-id>/liveserver.sqlite3`) with `sqlite3` — confirm what rows exist, whether the expected write happened, whether a partial transaction left junk. The DB is the second oracle when the log is silent.

## Step 2 — Reproduce at unit/integration level (red)

Write the smallest test that reproduces the hypothesis. Use the project's existing unit/integration conventions (here: pytest + the Django test client + `test_utils.py` seeders). The test should:
- Target the **faulty code path directly**, not the whole server.
- Set up **only** the precondition that triggers the bug (colliding rows, malformed payload, missing seed).
- Assert the **observable outcome** that's wrong (the raised exception, the wrong status, the corrupt row).

Example repro for the nick-collision hypothesis (this is a *unit/integration* test, runs in ~50ms):
```python
def test_dev_bypass_distinct_subs_share_nick_prefix_and_collide(db):
    """Repro: two DEV_AUTH_BYPASS_SUB values whose first 8 chars match derive
    the same nick and collide on identity_user_nick_ci_unique."""
    from src.domains.identity.models import User
    # auth0|dev-bypass  -> sub[:8] = "auth0|de"
    # auth0|dev-beta    -> sub[:8] = "auth0|de"  (same!)
    User.objects.create(sub="auth0|dev-bypass", nick="dev-auth0|de")
    with pytest.raises(IntegrityError):
        User.objects.create(sub="auth0|dev-beta", nick="dev-auth0|de")
```
This fails red immediately and points straight at the derivation logic — no server boot, no port, no logs.

## Step 3 — Fix (green)

Minimal production change. Resist fixing adjacent code; the repro test defines the scope. Re-run the repro test until green.

## Step 4 — Re-run the unit/integration suite

The fix may have broken a sibling — re-run the full unit/integration suite, not just the repro. Fix any cross-breakage in the code (never in the tests).

## Step 5 — Re-run the system test FRESH

This is the step that's easy to skip and fatal to skip. The unit repro proved the *cause*; only the system test proves the *assembled system* is fixed. **Re-run it in a brand-new `results/<run-id>/`** — clean DB, new process, fresh logs. A re-run against the old (now-corrupt) DB can both mask the bug and manufacture new ones.

## Step 6 — Land both tests

- The **unit/integration repro** stays in the suite as a fast, permanent regression guard.
- The **system test** stays as the blackbox contract pinning the end-to-end behavior.

Two tests, two tiers, one bug — permanently closed.

## Worked example: the dev-bypass nick-collision bug (S-01)

**System test (failed red):** `test_dev_bypass_colliding_sub_does_not_500` seeded `auth0|dev-bypass` (nick `dev-auth0|de`), booted the server with `DEV_AUTH_BYPASS_SUB=auth0|dev-beta`, hit `/v1/me`, got `500 != 200`. The captured stderr showed `IntegrityError: UNIQUE constraint failed: index 'identity_user_nick_ci_unique'` originating in `dev_auth_bypass._get_dev_user`'s `get_or_create`.

**Hypothesis:** The nick `f"dev-{sub[:8]}"` derives from only the first 8 chars of the sub. Auth0 subs share the `auth0|` prefix (6 of 8 chars), so distinct subs routinely produce identical 8-char slices → `get_or_create` INSERTs a colliding nick → `IntegrityError`.

**Unit repro (red, ~50ms):** Constructed two `User` rows with subs whose `[:8]` slices match, asserted the second `create` raises `IntegrityError`. Confirmed the hypothesis at unit tier without a server.

**Fix (green):** Changed the derivation to `f"dev-{uuid.uuid4().hex[:8]}"` — UUID entropy makes collisions practically impossible; mirrors the existing `_generated_nick()` pattern; harmless because `has_set_nick=False` prompts the user for a real nick anyway.

**Re-run unit/integration suite:** green, including the existing `test_dev_bypass.py` (its `nick.startswith("dev-")` assertion still holds).

**Re-run system test fresh:** `test_dev_bypass_colliding_sub_does_not_500` green in a clean DB. End-to-end confirmed.

**Landed:** the unit repro + the system test + the one-line fix. The bug class ("derive a unique-field default from a low-entropy input") is now captured in the skill's Gotchas so it's not relearned.

## Anti-patterns to avoid

- **"It works when I curl it manually"** — you curled against the dev DB (`src/db.sqlite3`) with stale rows that happen not to collide. The system test's fresh DB is the honest check.
- **Debugging only via the system test** — each run is slow and the cause is isolated poorly. Always drop to unit/integration first.
- **Patching the symptom, not the cause** — wrapping the `get_or_create` in a `try/except IntegrityError: pass` would silence the 500 but leave two users sharing a nick. The repro test catches this: the unit repro would still "pass" (no exception) but the *semantic* bug (two distinct subs, same nick) would remain. Assert on the semantic outcome, not just the absence of the exception.
- **Skipping the fresh re-run** — re-running against the old DB can falsely pass (the colliding row is already there, the new code path doesn't run) or falsely fail (leftover corrupt state). Always clean.
- **Committing the fix without the repro test** — without the unit repro, the next regression costs a full system-test run to detect instead of 50ms.
