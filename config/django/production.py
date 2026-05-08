
from .base import *
from config.env import env

DEBUG = env.bool('DJANGO_DEBUG', default=False)

# Use HTTPOnly and secure cookies in production
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True                  # True on HTTPS only (must enable in prod)
CSRF_COOKIE_SECURE = True

# Optional: set a dedicated subdomain for cookies if you use subdomains (leave None otherwise)
SESSION_COOKIE_DOMAIN = ".freewise.com"


R2_ACCESS_KEY_ID = env('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = env('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = env('R2_BUCKET_NAME')
R2_ENDPOINT_URL = env('R2_ENDPOINT_URL')
R2_CUSTOM_DOMAIN = env('R2_CUSTOM_DOMAIN')

AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL
AWS_S3_CUSTOM_DOMAIN = R2_CUSTOM_DOMAIN # optional: use a custom domain (Cloudflare CNAME) for nice URLs

# caching for static media (adjust TTL as you like)
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "public, max-age=31536000, immutable",
}

AWS_S3_SIGNATURE_VERSION = "s3v4" # signature version (keep)

# public files, no signed urls
AWS_QUERYSTRING_AUTH = False
AWS_DEFAULT_ACL = None

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "bucket_name": R2_BUCKET_NAME,
            "endpoint_url": R2_ENDPOINT_URL,   # used for API calls (put/get)
            "location": "media",                # where media files are stored inside the bucket
            # do NOT set custom_domain here for media if you want the regular bucket path URL
            "custom_domain": R2_CUSTOM_DOMAIN,
        },
    },
    "staticfiles": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "bucket_name": R2_BUCKET_NAME,
            "endpoint_url": R2_ENDPOINT_URL,   # keep this so boto3 talks to R2
            "location": "static",              # prefix in bucket
            # THIS is the important bit: tell storage to generate URLs with the public dev host
            "custom_domain": R2_CUSTOM_DOMAIN,
        },
    },
}

STATIC_URL = f"https://{R2_CUSTOM_DOMAIN}/{STATIC_LOCATION}/"

MEDIA_URL = f"https://{R2_CUSTOM_DOMAIN}/{MEDIA_LOCATION}/"
