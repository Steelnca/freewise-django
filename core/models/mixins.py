
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.utils import _generate_prefixed_public_id

class PublicIDMixin(models.Model):
    public_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name=_("public id"),
        help_text=_("Public ID used in URLs and sharing."),
    )

    PUBLIC_ID_PREFIX = "fwo" # Freewise Object
    PUBLIC_ID_LENGTH_PREFIX = 6

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = _generate_prefixed_public_id(
                prefix = self.PUBLIC_ID_PREFIX,
                model_cls = self.__class__,
                length = self.PUBLIC_ID_LENGTH_PREFIX,
                field_name = "public_id"
            )
        super().save(*args, **kwargs)


class LowercaseFieldsMixin:
    """ Mixin to lowercase fields defined in a model's Meta options."""

    def save(self, *args, **kwargs):
        options = getattr(self, "ProcessOptions", None)

        if options and hasattr(options, "lowercase_fields"):
            for field in options.lowercase_fields:
                value = getattr(self, field, None)
                if isinstance(value, str):  # Ensure the field exists and is a string
                    setattr(self, field, value.lower())
        super().save(*args, **kwargs)
