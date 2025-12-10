from django.urls import path

from . import views

urlpatterns = [
    path("currencies/", views.CurrencyListView.as_view(), name="currency-list"),
    path(
        "currencies/<int:pk>/",
        views.CurrencyDetailView.as_view(),
        name="currency-detail",
    ),
]
