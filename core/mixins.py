
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
