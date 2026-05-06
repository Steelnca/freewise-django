
import re

from better_profanity import profanity

from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils.translation import gettext as _

def validate_regex(value: str, field_name: str, pattern: str, error_message: str) -> None:
    """
    Validates a given value against a provided regex pattern.

    :param value: The value to validate.
    :param field_name: The name of the field being validated.
    :param pattern: The regex pattern to validate against.
    :param error_message: The error message to raise if validation fails.
    :raises ValidationError: If the value does not match the regex pattern.
    """
    regex = re.compile(pattern)
    if not regex.match(value):
        raise ValidationError(error_message, params={"field": field_name, "value": value})


def validate_reserved_terms(value, field_name="field"):
    # Check against reserved terms from settings
    reserved_terms = getattr(settings, "RESERVED_TERMS", [])
    if value.lower() in reserved_terms:
        raise ValidationError(
            _("The %(field)s '%(value)s' is reserved and cannot be used."),
            params={"field": field_name, "value": value},
        )

def validate_profanity(value, field_name="field"):
    # Load the default library and add custom words
    profanity.load_censor_words()

    # Validate the value
    if profanity.contains_profanity(value):
        raise ValidationError(
            _("The %(field)s '%(value)s' contains inappropriate or offensive words."),
            params={"field": field_name, "value": value},
        )
