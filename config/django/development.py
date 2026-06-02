from .base import *
from config.env import env

DEBUG = env.bool('DJANGO_DEBUG', default=True)

# for dev/testing
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

FREEWISE_WEBHOOK_BASE_URL = env("FREEWISE_WEBHOOK_BASE_URL", default="https://cute-deeply-opossum.ngrok-free.app")

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",
   "http://127.0.0.1:3000",
   "https://*.ngrok-free.app",
]