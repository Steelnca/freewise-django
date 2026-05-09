
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', include('pages.urls')),
    path('admin/', admin.site.urls),

    # API
    path('api/auth/',          include('users.urls')),
    path('api/accounts/',      include('accounts.urls')),
    path('api/freelancers/',   include('freelancers.urls')),
    path('api/clients/',       include('clients.urls')),
    path('api/jobs/',          include('jobs.urls')),
    path('api/offers/',        include('offers.urls')),
    path('api/contracts/',     include('contracts.urls')),
    path('api/payments/',      include('payments.urls')),
    path('api/collabs/',       include('collabs.urls')),
    path('api/reviews/',       include('reviews.urls')),
    path('api/notifications/', include('notifications.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)