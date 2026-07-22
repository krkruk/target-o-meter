"""
WSGI config for target_o_meter project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

# Project core is nested under src/; ensure the repository root is importable
# so that the `src.target_o_meter` package resolves.
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'src.target_o_meter.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
