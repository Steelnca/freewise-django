
from django.urls import path
from .views import SubmitReviewView, FreelancerReviewsView, ClientReviewsView

urlpatterns = [
    path('<int:contract_id>/',         SubmitReviewView.as_view(),       name='review-submit'),
    path('freelancer/<slug:slug>/',    FreelancerReviewsView.as_view(),  name='freelancer-reviews'),
    path('client/<slug:slug>/',        ClientReviewsView.as_view(),      name='client-reviews'),
]