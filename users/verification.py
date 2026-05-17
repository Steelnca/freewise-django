
"""
Verification utilities for email and phone OTP.
"""
import random
import string

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode


# ─── Email verification ───────────────────────────────────────────────────────

def generate_email_verification_link(user, request=None):
    """Generate a signed email verification URL."""
    uid   = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    base  = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
    return f"{base}/auth/verify-email/{uid}/{token}"


def send_verification_email(user):
    """Send email verification link to user."""
    link = generate_email_verification_link(user)
    send_mail(
        subject='Verify your Freewise email address',
        message=(
            f"Hi {user.username},\n\n"
            f"Please verify your email by clicking the link below:\n\n"
            f"{link}\n\n"
            f"This link expires in 24 hours.\n\n"
            f"If you didn't create an account, please ignore this email.\n\n"
            f"— The Freewise Team"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def verify_email_token(uidb64, token):
    """
    Verify an email token. Returns (user, error_message).
    On success: (user, None)
    On failure: (None, error_string)
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    try:
        uid  = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None, 'Invalid verification link.'

    if not default_token_generator.check_token(user, token):
        return None, 'Verification link is invalid or has expired.'

    return user, None


# ─── Phone OTP ────────────────────────────────────────────────────────────────

def _generate_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))


def send_phone_otp(account):
    """
    Generate a 6-digit OTP, store it, and send via SMS.
    Returns the PhoneOTP instance.

    SMS sending is abstracted — wire up your provider in settings:
        FREEWISE_SMS_BACKEND = 'users.sms_backends.TwilioBackend'
    or implement send_sms() below directly.
    """
    from .models import PhoneOTP

    if not account.phone:
        raise ValueError('No phone number on this account.')

    # Expire any previous OTPs
    PhoneOTP.objects.filter(account=account, used=False).update(used=True)

    code = _generate_otp()
    otp  = PhoneOTP.objects.create(account=account, code=code)

    # Send SMS — swap this for your provider
    _send_sms(str(account.phone), f'Your Freewise verification code is: {code}. Expires in 10 minutes.')

    return otp


def verify_phone_otp(account, code):
    """
    Verify a phone OTP. Returns (success, error_message).
    """
    from .models import PhoneOTP

    try:
        otp = PhoneOTP.objects.get(
            account=account,
            code=code,
            used=False,
        )
    except PhoneOTP.DoesNotExist:
        return False, 'Invalid or expired code.'

    if otp.is_expired():
        otp.used = True
        otp.save(update_fields=['used'])
        return False, 'Code has expired. Please request a new one.'

    otp.used = True
    otp.save(update_fields=['used'])
    account.phone_verified = True
    account.save(update_fields=['phone_verified'])
    return True, None


def _send_sms(phone_number: str, message: str):
    """
    Abstract SMS sender.
    Replace the body with your SMS provider SDK call.

    Twilio example:
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(body=message, from_=settings.TWILIO_FROM_NUMBER, to=phone_number)
    """
    import logging
    logger = logging.getLogger(__name__)

    # In development, just log the OTP
    if getattr(settings, 'DEBUG', True):
        logger.info(f'[SMS] To: {phone_number} | Message: {message}')
        return

    # Production: implement your SMS provider here
    provider = getattr(settings, 'FREEWISE_SMS_BACKEND', None)
    if not provider:
        logger.warning('FREEWISE_SMS_BACKEND not configured. SMS not sent.')
        return

    # Dynamic backend loading
    from importlib import import_module
    module_path, class_name = provider.rsplit('.', 1)
    module  = import_module(module_path)
    backend = getattr(module, class_name)()
    backend.send(phone_number, message)