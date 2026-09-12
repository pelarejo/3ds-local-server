from collections.abc import Callable
from functools import wraps

from django.contrib.auth import authenticate
from django.http import HttpRequest, HttpResponse

from threehs_backend.nb import ResultNamespace, ResultReason, result_response


def header_auth_required(namespace: ResultNamespace) -> Callable:
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = authenticate(
                request,
                username=request.headers.get("X-Auth-User"),
                password=request.headers.get("X-Auth-Password"),
            )
            if user is None or not user.is_active:
                return result_response(namespace, ResultReason.UNAUTHORIZED, "Invalid username or password.", status=401)
            request.user = user
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
