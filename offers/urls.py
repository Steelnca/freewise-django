from django.urls import path
from .views import SubmitOfferView, MyOffersView, JobOffersView, AcceptOfferView, WithdrawOfferView

urlpatterns = [
    path('mine/',                    MyOffersView.as_view(),    name='offer-mine'),
    path('job/<int:job_id>/',        JobOffersView.as_view(),   name='job-offers'),
    path('<int:job_id>/submit/',     SubmitOfferView.as_view(), name='offer-submit'),
    path('<int:offer_id>/accept/',   AcceptOfferView.as_view(), name='offer-accept'),
    path('<int:offer_id>/withdraw/', WithdrawOfferView.as_view(), name='offer-withdraw'),
]