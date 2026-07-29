"""Custom ``qcluster`` that shuts down cleanly on Ctrl+C / SIGTERM.

django-q2 issue #66 (still open as of 1.10.0): ``Cluster.sig_handler`` calls
``Cluster.stop()``, which is **not** idempotent. After the first successful
``stop()``, ``Cluster.stop_event`` is set to ``None`` (cluster.py:88) — but the
signal handler stays registered, so a duplicate signal (delivered more than
once under ``make dev``'s ``trap 'kill 0' INT`` process-group fan-out, and
again during interpreter atexit teardown) re-enters ``stop()`` and dereferences
the now-``None`` ``stop_event``:

    File ".../django_q/cluster.py", line 99, in sig_handler
        self.stop()
    File ".../django_q/cluster.py", line 84, in stop
        self.stop_event.set()
    AttributeError: 'NoneType' object has no attribute 'set'

NOTE on a related-but-separate symptom: CPython's
``multiprocessing.resource_tracker`` may still print a benign
"There appear to be N leaked semaphore objects to clean up at shutdown"
``UserWarning`` under the ``forkserver``/``multiprocessing.Queue`` interaction
(CPython #46391). That is NOT this bug — the tracker RECLAIMS the semaphores,
it just complains, and it fires even after a fully graceful shutdown. It is a
generic CPython limitation (also seen in sqlfluff, Apache Arrow, PyTorch) and
is left alone here: forcing ``set_start_method('fork')`` to silence it would
trade benign noise for real fork-after-thread / fork+OpenCV safety risks, which
is exactly what ``forkserver`` exists to avoid.

Two contributing causes of the #66 *crash* (complementary, same defect class):

* H1 — duplicate signal delivery. The handler is unguarded, so the second
  SIGINT crashes ``stop()`` on the nulled ``stop_event``.
* H2 — the upstream ``handle()`` does ``Cluster(); start()`` and returns
  *immediately* (no blocking loop, no ``try/except KeyboardInterrupt``).
  ``start()`` only blocks until ``start_event.is_set()``; the process then
  lingers on multiprocessing children + atexit. So the normal Ctrl+C path hands
  teardown to ``multiprocessing.util._exit_function``, which re-``join()``s the
  children — and a stray duplicate SIGINT during that join re-enters the
  live ``sig_handler`` and crashes.

This override keeps upstream's ``--run-once`` / ``-n/--name`` args and
behavior verbatim, then takes ownership of shutdown:

1. Re-register guarded, idempotent SIGINT/SIGTERM handlers after ``Cluster()``
   (which has already installed django-q's own).
2. Own the process lifetime by joining the sentinel in a loop instead of
   returning immediately (closes the H2 race window).
3. ``try/except KeyboardInterrupt`` + ``finally`` so the command exits 0 and
   never surfaces the atexit traceback.

Applies to both dev (``make dev``) and prod
(``docker-compose.prod.yml`` runs ``python src/manage.py qcluster``), so a
container ``stop`` (SIGTERM) also shuts down cleanly.

Override mechanism: same as the dev ``runserver`` override — Django resolves
management-command name collisions by reverse-iterating INSTALLED_APPS, so the
FIRST app that defines ``qcluster`` wins; ``src.target_o_meter`` (settings.py)
precedes ``django_q``, so this command resolves over the vendored one.
"""
from __future__ import annotations

import os
import signal

from django_q.cluster import Cluster
from django_q.management.commands.qcluster import Command as UpstreamCommand


def _safe_stop(q: Cluster) -> None:
    """Idempotent, race-safe wrapper around ``Cluster.stop()``.

    Guards the three unguarded attributes the upstream code nulled/mutated
    mid-shutdown (``stop_event``, ``start_event``, ``sentinel``) and swallows
    the library's own ``AttributeError`` (the very crash in #66) so a duplicate
    signal can never surface a traceback. Safe to call any number of times.
    """
    sentinel = getattr(q, "sentinel", None)
    if sentinel is None or not sentinel.is_alive():
        return
    if getattr(q, "stop_event", None) is None:
        return
    try:
        q.stop()
    except AttributeError:
        # Upstream nulls stop_event/start_event during stop(); a concurrent
        # re-entry can trip the None-deref (#66). We already ensured we won't
        # be the ones tripping it; this only absorbs a racing call. The
        # sentinel is joining in the main loop regardless.
        pass
    except Exception:  # noqa: BLE001 — shutdown must not raise
        pass


class Command(UpstreamCommand):
    """``qcluster`` with clean Ctrl+C / SIGTERM shutdown. See module docstring."""

    def handle(self, *args, **options):
        # Honor upstream's ``-n/--name`` (sets Q_CLUSTER_NAME before Cluster()).
        # Replicated verbatim from the parent so behavior is preserved.
        cluster_name = options.get("cluster_name")
        if cluster_name:
            os.environ["Q_CLUSTER_NAME"] = cluster_name

        q = Cluster()
        # Cluster.__init__ has already installed its own racy sig_handler for
        # SIGINT + SIGTERM. Replace both with our guarded handlers.
        self._q = q
        self._stopping = False
        signal.signal(signal.SIGINT, self._sig_handler)
        signal.signal(signal.SIGTERM, self._sig_handler)

        try:
            q.start()
            if options.get("run_once", False):
                _safe_stop(q)
                return
            # Own the process lifetime (H2): block until the sentinel exits.
            # Upstream returns here and relies on lingering children + atexit,
            # which is exactly the race window the crash lives in. A bounded
            # join timeout keeps us interruptible by signals between waits.
            sentinel = q.sentinel
            while sentinel is not None and sentinel.is_alive():
                try:
                    sentinel.join(timeout=0.5)
                except KeyboardInterrupt:
                    # First Ctrl+C arrived — unwind to finally for a clean stop.
                    break
        except KeyboardInterrupt:
            pass
        finally:
            _safe_stop(q)
            # Restore default dispositions so interpreter atexit teardown (and
            # any third signal) goes through normal Python/OS handling instead
            # of the now-defunct cluster handler.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _sig_handler(self, signum, frame):
        """First signal → clean stop (idempotent); second → force quit.

        Raising ``KeyboardInterrupt`` unwinds ``handle()``'s main loop so the
        try/finally runs (rather than leaving teardown to atexit, which is the
        #66 crash window). On a second signal we restore the default
        disposition and re-deliver so a second Ctrl+C force-quits immediately.
        """
        if not self._stopping:
            self._stopping = True
            # Best-effort, guarded stop — responsiveness. The finally block in
            # handle() calls _safe_stop again (a no-op once stopped).
            _safe_stop(self._q)
            raise KeyboardInterrupt
        # Second signal during shutdown: default-terminate immediately.
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
