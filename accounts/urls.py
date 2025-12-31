from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"transactions", views.TransactionViewSet, basename="transaction")
router.register(r"budgets", views.BudgetViewSet, basename="budget")
router.register(
    r"budget-categories", views.BudgetCategoryViewSet, basename="budget-category"
)
router.register(
    r"financial-goals", views.FinancialGoalViewSet, basename="financial-goal"
)
router.register(
    r"recurring-transactions",
    views.RecurringTransactionViewSet,
    basename="recurring-transaction",
)
router.register(r"analytics", views.AnalyticsViewSet, basename="analytics")
router.register(r"reports", views.ReportViewSet, basename="report")
router.register(r"", views.AccountViewSet, basename="account")


urlpatterns = router.urls
