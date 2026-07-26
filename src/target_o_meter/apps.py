from django.apps import AppConfig


class TargetOMeterConfig(AppConfig):
    """Project-level app — hosts the dev ``runserver`` override (Phase 5 Vite
    proxy) and the production-safety system checks. The domains and BFF stay
    separate; this app is for cross-cutting project infrastructure only."""
    name = 'src.target_o_meter'
    label = 'target_o_meter'
    default_auto_field = 'django.db.models.BigAutoField'
