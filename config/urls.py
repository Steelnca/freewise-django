from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from services.urls import orders_urlpatterns

urlpatterns = [
    path('', include('pages.urls')),
    path('admin/', admin.site.urls),

    # Accounts & Authentication
    path("accounts/", include("allauth.urls")),
    path("_allauth/", include("allauth.headless.urls")),

    # API — Auth
    path('api/auth/',          include('users.urls')),

    # API — Profiles
    path('api/accounts/',      include('accounts.urls')),
    path('api/freelancers/',   include('freelancers.urls')),
    path('api/clients/',       include('clients.urls')),

    # API — Marketplace
    path('api/jobs/',          include('jobs.urls')),
    path('api/proposals/',     include('proposals.urls')),
    path('api/services/',      include('services.urls')),
    path('api/orders/',        include((orders_urlpatterns, 'orders'))),

    # API — Contracts & Payments
    path('api/contracts/',     include('contracts.urls')),
    path('api/payments/',      include('payments.urls')),
    path("api/billing/", include("billing.urls")),

    # API — Community
    path('api/collabs/',       include('collabs.urls')),
    path('api/reviews/',       include('reviews.urls')),
    path('api/notifications/', include('notifications.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)