from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.http import HttpRequest, HttpResponse

from .models import HSAPIToken, User

admin.site.register(User, UserAdmin)


@admin.register(HSAPIToken)
class HSAPITokenAdmin(admin.ModelAdmin):
    """Create user-bound API tokens without retaining their raw credentials."""

    fields = ("name", "token_prefix", "created_at", "revoked_at")
    readonly_fields = ("token_prefix", "created_at")
    list_display = ("name", "user", "token_prefix", "created_at", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("name", "user__username", "token_prefix")

    def save_model(
        self, request: HttpRequest, obj: HSAPIToken, form, change: bool
    ) -> None:
        if not change:
            obj.user = request.user
            obj._raw_token = obj.set_token()
        super().save_model(request, obj, form, change)

    def response_add(
        self, request: HttpRequest, obj: HSAPIToken, post_url_continue=None
    ) -> HttpResponse:
        raw_token = getattr(obj, "_raw_token", None)
        if raw_token:
            self.message_user(
                request,
                f"Copy this API token now; it will not be shown again: {raw_token}",
            )
            del obj._raw_token
        return super().response_add(request, obj, post_url_continue)
