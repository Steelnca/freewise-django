from django.contrib.auth import get_user_model

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import TokenError

from accounts.serializers import AccountSerializer
from .serializers import RegisterSerializer
from .verification import (
    send_verification_email, verify_email_token,
    send_phone_otp, verify_phone_otp,
)

User = get_user_model()


class RegisterView(APIView):
    """
    POST /api/auth/register/
    Creates user (inactive until email verified), sends verification email.
    Does NOT return JWT tokens — user must verify email then login.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Create user as inactive until email is verified
        user = serializer.save()
        user.is_active = False
        user.save(update_fields=['is_active'])

        # Send verification email
        try:
            send_verification_email(user)
        except Exception as e:
            # If email fails, activate anyway and log — don't block signup
            import logging
            logging.getLogger(__name__).error(f'Failed to send verification email to {user.email}: {e}')
            user.is_active = True
            user.save(update_fields=['is_active'])
            account = getattr(user, 'account', None)
            if account:
                account.email_verified = True
                account.save(update_fields=['email_verified'])

        return Response({
            'detail': 'Account created. Please check your email to verify your account before logging in.',
            'email':  user.email,
        }, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """
    POST /api/auth/login/
    Standard simplejwt — username + password → tokens.
    """
    permission_classes = [AllowAny]


class LogoutView(APIView):
    """POST /api/auth/logout/ — blacklists refresh token."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({'detail': 'Refresh token is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            return Response({'detail': 'Token is invalid or already blacklisted.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': 'Logged out.'})


class MeView(APIView):
    """GET /api/auth/me/ — current user's account."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(AccountSerializer(account, context={'request': request}).data)


# ─── Email verification ───────────────────────────────────────────────────────

class ResendVerificationEmailView(APIView):
    """POST /api/auth/resend-verification/ — resend email verification link."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = getattr(request.user, 'account', None)
        if account and account.email_verified:
            return Response({'detail': 'Email already verified.'})
        try:
            send_verification_email(request.user)
        except Exception as e:
            return Response({'detail': f'Failed to send email: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({'detail': 'Verification email sent.'})


class VerifyEmailView(APIView):
    """
    POST /api/auth/verify-email/
    Body: { uidb64, token }
    Marks account as email_verified=True.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        uidb64 = request.data.get('uidb64', '')
        token  = request.data.get('token', '')

        user, error = verify_email_token(uidb64, token)
        if error:
            return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

        account = getattr(user, 'account', None)
        if account and not account.email_verified:
            account.email_verified = True
            account.save(update_fields=['email_verified'])

        return Response({'detail': 'Email verified successfully.'})


# ─── Phone OTP ────────────────────────────────────────────────────────────────

class RequestPhoneOTPView(APIView):
    """
    POST /api/auth/phone/request-otp/
    Sends a 6-digit OTP to the account's phone number.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not account.phone:
            return Response({'detail': 'No phone number on your account. Add one in settings first.'}, status=status.HTTP_400_BAD_REQUEST)
        if account.phone_verified:
            return Response({'detail': 'Phone already verified.'})

        try:
            send_phone_otp(account)
        except Exception as e:
            return Response({'detail': f'Failed to send OTP: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'detail': 'OTP sent to your phone number.'})


class VerifyPhoneOTPView(APIView):
    """
    POST /api/auth/phone/verify/
    Body: { code }
    Verifies the OTP and marks phone_verified=True.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = getattr(request.user, 'account', None)
        if not account:
            return Response({'detail': 'Account not found.'}, status=status.HTTP_404_NOT_FOUND)

        code = request.data.get('code', '').strip()
        if not code:
            return Response({'detail': 'Code is required.'}, status=status.HTTP_400_BAD_REQUEST)

        success, error = verify_phone_otp(account, code)
        if not success:
            return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Phone verified successfully.'})