from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"transactions", views.TransactionViewSet, basename="transaction")
router.register(r"budgets", views.BudgetViewSet, basename="budget")
router.register(
    r"financial-goals", views.FinancialGoalViewSet, basename="financial-goal"
)
router.register(r"", views.AccountViewSet, basename="account")


urlpatterns = router.urls
