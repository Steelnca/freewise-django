from django.urls import path
from .views import (
    RegisterView, LoginView, LogoutView, RefreshTokenView, MeView,
    ResendVerificationEmailView, VerifyEmailView,
    RequestPhoneOTPView, VerifyPhoneOTPView,
    ForgotPasswordView, ResetPasswordConfirmView, ChangePasswordView, AuthenticatedForgotPasswordView,

    CorsDebugView
)

urlpatterns = [
    # Auth
    path("cors/", CorsDebugView.as_view()),
    path('register/', RegisterView.as_view(),    name='auth-register'),
    path('login/',    LoginView.as_view(),        name='auth-login'),
    path('logout/',   LogoutView.as_view(),       name='auth-logout'),
    path('refresh/',  RefreshTokenView.as_view(), name='auth-refresh'),
    path('me/',       MeView.as_view(),           name='auth-me'),

    # Email verification
    path('verify-email/',        VerifyEmailView.as_view(), name='auth-verify-email'),
    path('resend-verification/', ResendVerificationEmailView.as_view(), name='auth-resend-verification'),

    # Phone OTP
    path('phone/request-otp/', RequestPhoneOTPView.as_view(), name='auth-phone-request-otp'),
    path('phone/verify/',      VerifyPhoneOTPView.as_view(),  name='auth-phone-verify'),

    path("forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ResetPasswordConfirmView.as_view(), name="auth-reset-password"),
    path("security/change-password/", ChangePasswordView.as_view(), name="auth-security-change-password"),
    path("security/forgot-password/", AuthenticatedForgotPasswordView.as_view(), name="auth-security-forgot-password"),
]