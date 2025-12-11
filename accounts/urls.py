from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"accounts", views.AccountViewSet, basename="account")
router.register(r"transactions", views.TransactionViewSet, basename="transaction")


urlpatterns = router.urls

# urlpatterns = [
#     path("", views.AccountListCreateView.as_view(), name="account-list-create"),
#     path("<uuid:pk>/", views.AccountDetailView.as_view(), name="account-detail"),
# ]
