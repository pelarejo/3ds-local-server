from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Project user model; intentionally small, but safe to extend later."""

    pass
