from django.urls import path

from . import views

urlpatterns = [
    path("", views.AccountListCreateView.as_view(), name="account-list-create"),
    path("<uuid:pk>/", views.AccountDetailView.as_view(), name="account-detail"),
]
