"""
URL configuration for target_o_meter project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from src.target_o_meter.health_views import health

urlpatterns = [
    path('admin/', admin.site.urls),
    # /health — Railway readiness probe (infrastructure-as-code P1). Routed
    # BEFORE the BFF include so the SPA-shell catch-all in bff.urls does not
    # swallow it; the prober has no session and needs the literal `ok`.
    path('health', health, name='health'),
    # BFF (auth routes + ninja API) — mounted first so /bff/* and /api/* win
    # over the index catch-all. SPA catch-all (Phase 5 / S-01) goes last.
    path('', include('src.bff.urls')),
]
