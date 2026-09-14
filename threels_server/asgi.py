"""ASGI config for the 3LS server."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "threels_server.settings")

application = get_asgi_application()
