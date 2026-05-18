from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    RegisterView, LoginView, LogoutView, MeView,
    ResendVerificationEmailView, VerifyEmailView,
    RequestPhoneOTPView, VerifyPhoneOTPView,
)

urlpatterns = [
    # Auth
    path('register/', RegisterView.as_view(),    name='auth-register'),
    path('login/',    LoginView.as_view(),        name='auth-login'),
    path('logout/',   LogoutView.as_view(),       name='auth-logout'),
    path('refresh/',  TokenRefreshView.as_view(), name='auth-refresh'),
    path('me/',       MeView.as_view(),           name='auth-me'),

    # Email verification
    path('verify-email/',        VerifyEmailView.as_view(),             name='auth-verify-email'),
    path('resend-verification/', ResendVerificationEmailView.as_view(), name='auth-resend-verification'),

    # Phone OTP
    path('phone/request-otp/', RequestPhoneOTPView.as_view(), name='auth-phone-request-otp'),
    path('phone/verify/',      VerifyPhoneOTPView.as_view(),  name='auth-phone-verify'),
]