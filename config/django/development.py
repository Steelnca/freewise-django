from .base import *
from config.env import env

DEBUG = env.bool('DJANGO_DEBUG', default=True)

# for dev/testing
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"