from django.urls import path, include

from . import views
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r"currencies", views.CurrencyViewSet, basename="currency")
router.register(r"categories", views.CategoryViewSet, basename="category")


urlpatterns = [
    path("", include(router.urls)),
]
