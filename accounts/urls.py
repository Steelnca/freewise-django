
from django.urls import path
from .views import AccountMeView, ActivateRoleView

urlpatterns = [
    path('me/',            AccountMeView.as_view(),    name='account-me'),
    path('activate-role/', ActivateRoleView.as_view(), name='account-activate-role'),
]