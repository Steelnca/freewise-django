from .base import *
from config.env import env

DEBUG = env.bool('DJANGO_DEBUG', default=True)

# for dev/testing
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

FRONTEND_URL = "http://localhost:3000"

HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": "http://localhost:3000/auth/verify-email/{key}",
}