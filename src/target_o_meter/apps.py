import faulthandler

from django.apps import AppConfig


class TargetOMeterConfig(AppConfig):
    """Project-level app — hosts the dev ``runserver`` override (Phase 5 Vite
    proxy) and the production-safety system checks. The domains and BFF stay
    separate; this app is for cross-cutting project infrastructure only."""
    name = 'src.target_o_meter'
    label = 'target_o_meter'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        """Enable ``faulthandler`` so a native crash (segfault) in a C extension
        — opencv/numpy under the vision pipeline — prints a real Python
        traceback to stderr instead of dying silently.

        Without this, the q2 worker's fast silent death during
        ``GeometryPipeline.run`` (Phase 8.12: dies ~1s after ``upload read
        back``, no ``SIGKILL``/OOM signature, no ``process_image failed`` line)
        is invisible: the process is killed by the OS before Python's
        ``except Exception`` in ``process_image`` can run, so ``logger.exception``
        never fires and ``reincarnated worker … after death`` is the only
        signal. ``faulthandler.enable()`` installs a SIGSEGV/SIGFPE/SIGABRT
        handler that dumps the current Python stack to stderr, turning a silent
        native crash into a locatable one. Enabled for every process — gunicorn,
        qcluster, and the q2 worker subprocesses (which fork after ready()).
        Cheap (one installed handler); no per-request cost.
        """
        faulthandler.enable()
