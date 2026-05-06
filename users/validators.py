
from functools import partial

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from core import validators

from .constants import USERNAME_MIN_LENGTH, USERNAME_MAX_LENGTH

username_regex = partial(
    validators.validate_regex,
    pattern=fr"^[\w-]{{{USERNAME_MIN_LENGTH},{USERNAME_MAX_LENGTH}}}$",
    error_message=(
        f"Username must be between {USERNAME_MIN_LENGTH} and "
        f"{USERNAME_MAX_LENGTH} characters long, and may contain only "
        "letters, numbers, underscores (_), and hyphens (-)."
    ),
    field_name="username",
)

username_profanity = partial(
    validators.validate_profanity,
    field_name = 'username'
)

username_reserved_terms = partial(
    validators.validate_reserved_terms,
    field_name = 'username'
)