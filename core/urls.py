from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"currencies", views.CurrencyViewSet, basename="currency")
router.register(r"categories", views.CategoryViewSet, basename="category")


urlpatterns = [
    path("", include(router.urls)),
]
