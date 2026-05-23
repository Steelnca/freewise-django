import logging

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.account.utils import send_email_confirmation

from accounts.serializers import AccountSerializer
from .serializers import RegisterSerializer
from .verification import (
    send_phone_otp,
    verify_phone_otp,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def get_user_account(user):
    return getattr(user, "account", None)


def ensure_primary_email_address(user):
    """
    Keep allauth's EmailAddress table in sync with your user model.
    This is the source of truth for email verification state.
    """
    email = (getattr(user, "email", "") or "").strip().lower()
    if not email:
        return None

    email_address, created = EmailAddress.objects.get_or_create(
        user=user,
        email=email,
        defaults={
            "primary": True,
            "verified": False,
        },
    )

    changed = False
    if not email_address.primary:
        email_address.primary = True
        changed = True

    if email_address.email != email:
        email_address.email = email
        changed = True

    if changed:
        email_address.save(update_fields=["primary", "email"])

    return email_address


def user_has_verified_email(user):
    email = (getattr(user, "email", "") or "").strip().lower()

    qs = EmailAddress.objects.filter(user=user, verified=True)
    if email:
        qs = qs.filter(email__iexact=email)

    return qs.exists() or EmailAddress.objects.filter(user=user, verified=True).exists()


class RegisterView(APIView):
    """
    POST /api/auth/register/

    Creates the user and sends an allauth verification email.
    Does not manually flip any custom email_verified field.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()
        ensure_primary_email_address(user)

        try:
            send_email_confirmation(request, user, signup=True)
        except Exception:
            logger.exception("Failed to send verification email to %s", user.email)

        return Response(
            {
                "detail": (
                    "Account created. Check your email to verify it before logging in."
                ),
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    """
    POST /api/auth/login/

    Standard SimpleJWT login, but blocked until allauth says the email is verified.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.user
        if not user_has_verified_email(user):
            return Response(
                {
                    "detail": "Please verify your email before logging in."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    POST /api/auth/logout/
    Blacklists the refresh token.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"detail": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            return Response(
                {"detail": "Token is invalid or already blacklisted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"detail": "Logged out."})


class MeView(APIView):
    """
    GET /api/auth/me/
    Returns the current user's account data.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account = get_user_account(request.user)
        if not account:
            return Response(
                {"detail": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            AccountSerializer(account, context={"request": request}).data
        )


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        key = request.data.get("key")

        if not key:
            return Response(
                {"detail": "Verification key is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        confirmation = EmailConfirmationHMAC.from_key(key)

        if not confirmation:
            return Response(
                {"detail": "Invalid or expired verification link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        confirmation.confirm(request)

        return Response(
            {"detail": "Email verified successfully."},
            status=status.HTTP_200_OK,
        )

class ResendVerificationEmailView(APIView):
    """
    POST /api/auth/resend-verification/

    Authenticated:
        Resends to the logged-in user's email.

    Unauthenticated:
        Accepts { email } and responds generically.
        No account existence leakage.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        if request.user and request.user.is_authenticated:
            user = request.user
        else:
            email = (request.data.get("email") or "").strip().lower()
            if not email:
                return Response(
                    {"detail": "Email is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = User.objects.filter(email__iexact=email).first()
            if not user:
                return Response(
                    {
                        "detail": (
                            "If that email exists, a verification link will be sent."
                        )
                    },
                    status=status.HTTP_200_OK,
                )

        ensure_primary_email_address(user)

        if user_has_verified_email(user):
            return Response(
                {"detail": "Email is already verified."},
                status=status.HTTP_200_OK,
            )

        try:
            send_email_confirmation(request, user, signup=False)
        except Exception:
            logger.exception("Failed to resend verification email to %s", user.email)
            return Response(
                {"detail": "Failed to send verification email."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"detail": "Verification email sent. Please check your inbox."},
            status=status.HTTP_200_OK,
        )


class RequestPhoneOTPView(APIView):
    """
    POST /api/auth/phone/request-otp/
    Sends a 6-digit OTP to the account's phone number.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = get_user_account(request.user)
        if not account:
            return Response(
                {"detail": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not account.phone:
            return Response(
                {"detail": "No phone number on your account. Add one first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if account.phone_verified:
            return Response(
                {"detail": "Phone already verified."},
                status=status.HTTP_200_OK,
            )

        try:
            send_phone_otp(account)
        except Exception:
            logger.exception("Failed to send phone OTP for user %s", request.user.id)
            return Response(
                {"detail": "Failed to send OTP."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({"detail": "OTP sent to your phone number."})


class VerifyPhoneOTPView(APIView):
    """
    POST /api/auth/phone/verify/
    Body: { code }
    Verifies the OTP and marks phone_verified=True.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = get_user_account(request.user)
        if not account:
            return Response(
                {"detail": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        code = (request.data.get("code") or "").strip()
        if not code:
            return Response(
                {"detail": "Code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, error = verify_phone_otp(account, code)
        if not success:
            return Response(
                {"detail": error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"detail": "Phone verified successfully."})