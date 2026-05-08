
from django.urls import path
from .views import ClientProfileMeView, ClientProfileDetailView

urlpatterns = [
    path('me/',          ClientProfileMeView.as_view(),    name='client-me'),
    path('<slug:slug>/', ClientProfileDetailView.as_view(), name='client-detail'),
]