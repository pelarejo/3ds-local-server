from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse

from threels_server.nb import ResultNamespace, ResultReason, result_response

from .models import HSAPIToken


def header_auth_required(namespace: ResultNamespace) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            username = request.headers.get("X-Auth-User", "")
            raw_token = request.headers.get("X-Auth-Password", "")
            try:
                token_hash = HSAPIToken.hash_token(raw_token)
            except ValueError:
                token = None
            else:
                token = (
                    HSAPIToken.objects.select_related("user")
                    .filter(
                        token_hash=token_hash,
                        revoked_at__isnull=True,
                        user__username=username,
                        user__is_active=True,
                    )
                    .first()
                )
            if token is None:
                return result_response(
                    namespace,
                    ResultReason.UNAUTHORIZED,
                    "Invalid username or API token.",
                    status=401,
                )
            request.user = token.user
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
