from django.urls import path
from .views import (
    ServiceListView, ServiceDetailView,
    MyServicesView, ServiceCreateView, ServiceUpdateView, ServiceDeleteView,
    PlaceOrderView,
    MyOrdersView, OrderDetailView,
    DeliverOrderView, ApproveOrderView, DisputeOrderView,
)

urlpatterns = [
    # Public
    path('',               ServiceListView.as_view(),   name='service-list'),
    path('mine/',          MyServicesView.as_view(),    name='service-mine'),
    path('create/',        ServiceCreateView.as_view(), name='service-create'),
    path('<int:pk>/',      ServiceDetailView.as_view(), name='service-detail'),
    path('<int:pk>/edit/', ServiceUpdateView.as_view(), name='service-update'),
    path('<int:pk>/delete/', ServiceDeleteView.as_view(), name='service-delete'),
    path('<int:pk>/order/', PlaceOrderView.as_view(),   name='service-order'),
]

orders_urlpatterns = [
    path('mine/',              MyOrdersView.as_view(),    name='order-mine'),
    path('<int:pk>/',          OrderDetailView.as_view(), name='order-detail'),
    path('<int:pk>/deliver/',  DeliverOrderView.as_view(),name='order-deliver'),
    path('<int:pk>/approve/',  ApproveOrderView.as_view(),name='order-approve'),
    path('<int:pk>/dispute/',  DisputeOrderView.as_view(),name='order-dispute'),
]
