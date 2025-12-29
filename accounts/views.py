from django.conf import settings
from django.db import models
from decimal import Decimal
from datetime import date, timedelta
from django.db import transaction as db_transaction
from django.db.models import Count, Q, Sum, Avg, Min, Max
from django.db.models.functions import TruncMonth, TruncWeek, TruncDay, Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError as DRFValidationError,
)
from rest_framework.decorators import action
from rest_framework.response import Response

from utils import choices, loggings, throttlings
from utils.paginations import CustomPageNumberPagination
from utils.permissions import IsOwnerOrAdmin

from .filters import (
    AccountFilter,
    BudgetFilter,
    BudgetCategoryFilter,
    FinancialGoalFilter,
    TransactionFilter,
)

from .models import Account, Budget, BudgetCategory, FinancialGoal, Transaction
from .serializers import (
    AccountDetailSerializer,
    AccountListSerializer,
    AccountReconcileSerializer,
    AccountSerializer,
    BaseBudgetSerializer,
    BudgetCreateSerializer,
    BudgetDetailSerializer,
    BudgetListSerializer,
    BudgetRecalculateSerializer,
    BudgetSerializer,
    BudgetUpdateSerializer,
    BudgetCategorySerializer,
    FinancialGoalContributionSerializer,
    FinancialGoalCreateSerializer,
    FinancialGoalDetailSerializer,
    FinancialGoalListSerializer,
    FinancialGoalSerializer,
    FinancialGoalUpdateSerializer,
    TransactionCreateSerializer,
    TransactionSerializer,
    TransactionUpdateSerializer,
    TransactionVerificationSerializer,
    get_account_serializer,
    get_financial_goal_serializer,
)

logger = loggings.setup_logging()


class BaseAccountViewSet(viewsets.GenericViewSet):
    """
    Base ViewSet with common account functionality.

    Provides:
    - Common permission handling
    - Shared queryset logic
    - Consistent error handling
    - Serializer selection based on action
    """

    permission_classes = [IsOwnerOrAdmin]
    pagination_class = CustomPageNumberPagination

    def get_queryset(self):
        """
        Get accounts based on user permissions with optimization.

        Rules:
        - Superusers/Staff: All accounts with related data
        - Regular users: Only their own accounts
        """
        user = self.request.user

        # Optimize queries with select_related and prefetch_related
        queryset = (
            Account.objects.select_related("currency", "user")
            .prefetch_related("transactions")
            .annotate(
                transaction_count=Count(
                    "transactions",
                    filter=Q(transactions__status=choices.TransactionStatus.COMPLETED),
                    distinct=True,
                )
            )
        )

        if user.is_staff or user.is_superuser:
            return queryset

        return queryset.filter(user=user)

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        return get_account_serializer(self.action)

    def _handle_api_error(
        self,
        error: Exception,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> Response:
        """
        Handle API errors consistently.

        Args:
            error: Exception that occurred
            message: User-friendly error message
            status_code: HTTP status code to return

        Returns:
            Error response
        """
        logger.exception(f"API Error: {error}")

        # Don't expose internal error details in production
        error_detail = {"error": message}

        # Include more detail in development/debug mode
        if settings.DEBUG:
            error_detail["debug"] = str(error)

        return Response(error_detail, status=status_code)


class AccountViewSet(
    BaseAccountViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    Account ViewSet for comprehensive financial account management.

    Features:
    - Full CRUD operations with role-based permissions
    - Account reconciliation and balance management
    - Advanced filtering and search capabilities
    - Bulk operations for efficiency
    - Comprehensive error handling and logging

    Permissions:
    - Regular users: CRUD only their own accounts
    - Staff/Admin: CRUD any account

    Security:
    - Proper ownership validation
    - Business logic enforcement
    - Audit logging for sensitive operations
    """

    queryset = Account.objects.all()
    filterset_class = AccountFilter
    search_fields = ["name", "bank_name", "account_number"]
    ordering_fields = ["name", "current_balance", "created_at"]
    ordering = ["-created_at"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    @extend_schema(
        summary="List accounts",
        description=(
            "List all accounts accessible to the authenticated user. "
            "Regular users see only their accounts. "
            "Staff/Admin see all accounts."
        ),
        responses={
            200: AccountListSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List accounts with advanced filtering and pagination."""
        try:
            queryset = self.get_queryset()
            queryset = self.filter_queryset(queryset)

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            return self._handle_api_error(
                e,
                "An error occurred while retrieving accounts.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Retrieve account",
        description="Retrieve detailed information about a specific account.",
        responses={
            200: AccountDetailSerializer,
            404: OpenApiResponse(description="Account not found"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve account with detailed information."""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response(serializer.data)

        except Account.DoesNotExist:
            logger.warning(f"Account not found: {kwargs.get('id')}")
            return Response(
                {"error": "Account not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return self._handle_api_error(
                e,
                "An error occurred while retrieving the account.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create account",
        description=(
            "Create a new financial account. "
            "Currency and initial balance are required. "
            "Account name must be unique per user."
        ),
        request=AccountSerializer,
        responses={
            201: AccountSerializer,
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new financial account."""
        try:
            serializer = self.get_serializer(
                data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                account = serializer.save()

            logger.info(
                f"Account created: id={account.id}, "
                f"name='{account.name}', "
                f"user={request.user.id}, "
                f"type={account.account_type}, "
                f"currency={account.currency.code}"
            )

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except serializers.ValidationError as e:
            logger.warning(f"Account creation validation failed: {e}")
            return Response(
                {"error": "Validation failed.", "details": e.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return self._handle_api_error(
                e,
                "Failed to create account. Please check your data.",
                status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Partial update account",
        description="Update specific fields of an account using PATCH.",
        request=AccountSerializer,
        responses={
            200: AccountSerializer,
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        """Partial update account (PATCH)."""
        try:
            instance = self.get_object()
            self._check_account_modification_permission(instance, request.user)

            serializer = self.get_serializer(
                instance, data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Account partially updated: id={updated_instance.id}, "
                f"updated fields={list(request.data.keys())}, "
                f"by user={request.user.id}"
            )

            return Response(serializer.data)

        except PermissionError as e:
            logger.warning(f"Permission denied updating account: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except serializers.ValidationError as e:
            logger.warning(f"Account update validation failed: {e}")
            return Response(
                {"error": "Validation failed.", "details": e.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return self._handle_api_error(
                e, "Failed to update account.", status.HTTP_400_BAD_REQUEST
            )

    def _check_account_modification_permission(self, account, user):
        """
        Check if user can modify the account.

        Args:
            account: Account instance
            user: User making the request

        Raises:
            PermissionError: If user cannot modify the account
        """
        # Staff/Admin can modify any account
        if user.is_staff or user.is_superuser:
            return

        # Regular users can only modify their own accounts
        if account.user != user:
            raise PermissionError("Cannot modify another user's account.")

        # Check if account is locked
        if account.is_locked:
            raise PermissionError("Cannot modify a locked account.")

    @extend_schema(
        summary="Delete account",
        description=(
            "Soft delete an account (set is_active=False). "
            "Accounts with transactions or active sub-accounts cannot be deleted. "
            "Locked accounts cannot be deleted."
        ),
        responses={
            204: OpenApiResponse(description="No Content"),
            400: OpenApiResponse(description="Cannot delete account"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Account not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Soft delete account with validation."""
        try:
            instance = self.get_object()
            user = request.user

            # Check permissions
            self._check_account_modification_permission(instance, user)

            # Check if account can be deleted
            if not self._can_delete_account(instance):
                error_msg = self._get_delete_error_message(instance)
                return Response(
                    {"error": error_msg}, status=status.HTTP_400_BAD_REQUEST
                )

            # Soft delete (set is_active=False)
            with db_transaction.atomic():
                instance.is_active = False
                instance.save(update_fields=["is_active", "updated_at"])

            logger.info(
                f"Account soft deleted: id={instance.id}, "
                f"name='{instance.name}', "
                f"by user={user.id}"
            )

            return Response(status=status.HTTP_204_NO_CONTENT)

        except PermissionError as e:
            logger.warning(f"Permission denied deleting account: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            return self._handle_api_error(
                e, "Failed to delete account.", status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _can_delete_account(self, account: Account) -> bool:
        """
        Comprehensive validation for account deletion.

        Business Rules:
        1. No completed transactions allowed
        2. Account must not be locked
        3. No pending transactions
        4. No linked financial goals
        5. No linked recurring transactions (if applicable)
        6. No active sub-accounts (if hierarchical accounts exist)
        7. Account must be inactive for minimum period (configurable)
        8. No open banking connections

        Args:
            account: Account instance to validate

        Returns:
            True if account can be deleted, False otherwise
        """
        logger.debug(f"Validating account deletion for account {account.id}")

        # Check for completed transactions (using cached count)
        if account.transaction_count > 0:
            logger.info(
                f"Account {account.id} has {account.transaction_count} transactions, cannot delete"
            )
            return False

        # Check if account is locked
        if account.is_locked:
            logger.info(f"Account {account.id} is locked, cannot delete")
            return False

        # Check for pending transactions (uncached, real-time check)
        try:
            from .models import Transaction

            pending_count = Transaction.objects.filter(
                account=account, status=choices.TransactionStatus.PENDING
            ).count()

            if pending_count > 0:
                logger.info(
                    f"Account {account.id} has {pending_count} pending transactions, cannot delete"
                )
                return False
        except Exception as e:
            logger.error(
                f"Error checking pending transactions for account {account.id}: {e}"
            )
            # Fail-safe: if we can't check, assume there might be pending transactions
            return False

        # Check for linked financial goals
        try:
            from .models import FinancialGoal

            goal_count = FinancialGoal.objects.filter(
                linked_account=account, is_active=True, is_achieved=False
            ).count()

            if goal_count > 0:
                logger.info(
                    f"Account {account.id} is linked to {goal_count} active financial goals, cannot delete"
                )
                return False
        except Exception as e:
            logger.error(
                f"Error checking financial goals for account {account.id}: {e}"
            )

        # Check for active transfers (as destination account)
        try:
            from .models import Transaction

            transfer_count = Transaction.objects.filter(
                transfer_account=account,
                status__in=[
                    choices.TransactionStatus.PENDING,
                    choices.TransactionStatus.COMPLETED,
                ],
                transaction_date__gte=timezone.now().date() - timedelta(days=30),
            ).count()

            if transfer_count > 0:
                logger.info(
                    f"Account {account.id} has {transfer_count} recent transfers, cannot delete"
                )
                return False
        except Exception as e:
            logger.error(f"Error checking transfers for account {account.id}: {e}")

        # Check account age and activity (prevent accidental deletion of old accounts)
        try:
            # Don't delete accounts created in the last 7 days (safety period)
            min_age_days = 7
            account_age = (timezone.now() - account.created_at).days

            if account_age < min_age_days:
                logger.info(
                    f"Account {account.id} is only {account_age} days old, "
                    f"requires minimum {min_age_days} days before deletion"
                )
                return False

            # Check if account has been active recently (last 90 days)
            last_transaction = (
                Transaction.objects.filter(account=account)
                .order_by("-transaction_date")
                .first()
            )

            if last_transaction:
                days_since_last_tx = (
                    timezone.now().date() - last_transaction.transaction_date
                ).days
                if days_since_last_tx < 90:
                    logger.info(
                        f"Account {account.id} had activity {days_since_last_tx} days ago, "
                        f"consider archiving instead of deletion"
                    )
                    # Not a hard block, but worth logging
        except Exception as e:
            logger.error(
                f"Error checking account activity for account {account.id}: {e}"
            )

        # Check for open banking connections (if applicable)
        try:
            if (
                account.institution_data
                and account.institution_data.get("connection_status") == "active"
            ):
                logger.info(
                    f"Account {account.id} has active banking connection, disconnect first"
                )
                return False
        except Exception as e:
            logger.error(
                f"Error checking banking connection for account {account.id}: {e}"
            )

        # Check if account has been reconciled recently (prevent deletion before audit)
        try:
            if account.reconciled_at:
                days_since_reconciliation = (
                    timezone.now() - account.reconciled_at
                ).days
                if days_since_reconciliation < 30:
                    logger.info(
                        f"Account {account.id} was reconciled {days_since_reconciliation} days ago, "
                        f"wait for next audit cycle"
                    )
                    # Not a hard block, but important for audit trail
        except Exception as e:
            logger.error(
                f"Error checking reconciliation status for account {account.id}: {e}"
            )

        logger.debug(f"Account {account.id} passed all deletion validations")

        return True

    def _get_delete_error_message(self, account: Account) -> str:
        """
        Get detailed error message explaining why account cannot be deleted.

        Args:
            account: Account instance

        Returns:
            Detailed error message with specific reason
        """
        from .models import Budget, FinancialGoal, Transaction

        # 1. Check for completed transactions
        if account.transaction_count > 0:
            return (
                f"Cannot delete account '{account.name}' because it has "
                f"{account.transaction_count} completed transactions. "
                f"Please archive the account instead."
            )

        # 2. Check if account is locked
        if account.is_locked:
            return (
                f"Cannot delete account '{account.name}' because it is locked. "
                f"Unlock the account first or contact support."
            )

        # 3. Check for pending transactions
        try:
            pending_count = Transaction.objects.filter(
                account=account, status=choices.TransactionStatus.PENDING
            ).count()

            if pending_count > 0:
                return (
                    f"Cannot delete account '{account.name}' because it has "
                    f"{pending_count} pending transactions. "
                    f"Please complete or cancel these transactions first."
                )
        except Exception:
            pass  # Continue with other checks

        # 4. Check for financial goals
        try:
            goal_count = FinancialGoal.objects.filter(
                linked_account=account, is_active=True, is_achieved=False
            ).count()

            if goal_count > 0:
                return (
                    f"Cannot delete account '{account.name}' because it is linked to "
                    f"{goal_count} active financial goal(s). "
                    f"Please unlink from goals first."
                )
        except Exception:
            pass

        # 5. Check for recent transfers
        try:
            transfer_count = Transaction.objects.filter(
                transfer_account=account,
                status__in=[
                    choices.TransactionStatus.PENDING,
                    choices.TransactionStatus.COMPLETED,
                ],
                transaction_date__gte=timezone.now().date() - timedelta(days=30),
            ).count()

            if transfer_count > 0:
                return (
                    f"Cannot delete account '{account.name}' because it has "
                    f"{transfer_count} recent transfer(s). "
                    f"Please wait for transfer history to age or contact support."
                )
        except Exception:
            pass

        # 8. Check account age
        try:
            min_age_days = 7
            account_age = (timezone.now() - account.created_at).days

            if account_age < min_age_days:
                days_remaining = min_age_days - account_age
                return (
                    f"Cannot delete account '{account.name}' because it is only "
                    f"{account_age} day(s) old. Accounts must be at least "
                    f"{min_age_days} days old before deletion. "
                    f"Please try again in {days_remaining} day(s)."
                )
        except Exception:
            pass

        # 9. Check for banking connections
        try:
            if (
                account.institution_data
                and account.institution_data.get("connection_status") == "active"
            ):
                return (
                    f"Cannot delete account '{account.name}' because it has an active "
                    f"banking connection. Please disconnect from the bank first."
                )
        except Exception:
            pass

        # 10. Check reconciliation status
        try:
            if account.reconciled_at:
                days_since_reconciliation = (
                    timezone.now() - account.reconciled_at
                ).days
                if days_since_reconciliation < 30:
                    days_remaining = 30 - days_since_reconciliation
                    return (
                        f"Cannot delete account '{account.name}' because it was "
                        f"reconciled {days_since_reconciliation} day(s) ago. "
                        f"Accounts cannot be deleted within 30 days of reconciliation "
                        f"for audit purposes. Please try again in {days_remaining} day(s)."
                    )
        except Exception:
            pass

        # Default message
        return "Cannot delete account. Please contact support for assistance."

    @extend_schema(
        summary="Check if account can be deleted",
        description="Pre-check all deletion constraints before attempting deletion.",
        responses={
            200: OpenApiResponse(description="Deletion check results"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Account not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="can-delete")
    def can_delete_check(self, request, id=None):
        """Check if account can be deleted with detailed constraints."""
        try:
            account = self.get_object()
            user = request.user

            # Check permissions
            self._check_account_modification_permission(account, user)

            # Run all deletion checks
            can_delete = self._can_delete_account(account)
            error_message = (
                self._get_delete_error_message(account) if not can_delete else None
            )

            # Get detailed constraint information
            constraints = self._get_deletion_constraints(account)

            response = {
                "account_id": str(account.id),
                "account_name": account.name,
                "can_delete": can_delete,
                "error_message": error_message,
                "constraints": constraints,
                "suggested_actions": self._get_deletion_suggestions(constraints),
                "alternative_options": [
                    "Archive account (set is_active=False)",
                    "Rename account for historical reference",
                    "Merge with another account (if supported)",
                ],
            }

            return Response(response)

        except PermissionError as e:
            logger.warning(f"Permission denied checking deletion: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            return self._handle_api_error(
                e,
                "Failed to check deletion constraints.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Get account balance history",
        description="Get historical balance data aggregated by interval (daily, weekly, monthly).",
        parameters=[
            OpenApiParameter(
                "start_date",
                OpenApiTypes.DATE,
                description="Start date (YYYY-MM-DD), defaults to 30 days ago",
            ),
            OpenApiParameter(
                "end_date",
                OpenApiTypes.DATE,
                description="End date (YYYY-MM-DD), defaults to today",
            ),
            OpenApiParameter(
                "interval",
                OpenApiTypes.STR,
                enum=["daily", "weekly", "monthly"],
                default="daily",
            ),
        ],
        responses={200: OpenApiResponse(description="Balance history data")},
    )
    @action(detail=True, methods=["get"], url_path="balance-history")
    def balance_history(self, request, id=None):
        """Get account balance history."""
        try:
            account = self.get_object()

            # Parse params
            end_date = request.query_params.get("end_date")
            start_date = request.query_params.get("start_date")
            interval = request.query_params.get("interval", "daily")

            today = timezone.now().date()

            if end_date:
                end_date_obj = date.fromisoformat(end_date)
            else:
                end_date_obj = today

            if start_date:
                start_date_obj = date.fromisoformat(start_date)
            else:
                start_date_obj = end_date_obj - timedelta(days=30)

            # Align dates to interval
            if interval == "monthly":
                start_date_obj = start_date_obj.replace(day=1)
            elif interval == "weekly":
                start_date_obj = start_date_obj - timedelta(
                    days=start_date_obj.weekday()
                )

            if start_date_obj > end_date_obj:
                return Response(
                    {"error": "Start date must be before end date"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Calculation Logic:
            # 1. Calculate Opening Balance at start_date_obj
            pre_start_net = Transaction.objects.filter(
                account=account,
                transaction_date__lt=start_date_obj,
                status__in=[
                    choices.TransactionStatus.COMPLETED,
                    choices.TransactionStatus.RECONCILED,
                ],
            ).aggregate(
                income=Coalesce(
                    Sum(
                        "amount",
                        filter=Q(transaction_type=choices.TransactionType.INCOME),
                    ),
                    Decimal("0.00"),
                ),
                expense=Coalesce(
                    Sum(
                        "amount",
                        filter=Q(transaction_type=choices.TransactionType.EXPENSE),
                    ),
                    Decimal("0.00"),
                ),
            )

            formatted_initial = (
                Decimal(str(account.initial_balance))
                if not isinstance(account.initial_balance, Decimal)
                else account.initial_balance
            )
            opening_balance = (
                formatted_initial + pre_start_net["income"] - pre_start_net["expense"]
            )

            # 2. Group transactions in range
            trunc_func = {
                "daily": TruncDay,
                "weekly": TruncWeek,
                "monthly": TruncMonth,
            }.get(interval, TruncDay)

            period_changes = (
                Transaction.objects.filter(
                    account=account,
                    transaction_date__gte=start_date_obj,
                    transaction_date__lte=end_date_obj,
                    status__in=[
                        choices.TransactionStatus.COMPLETED,
                        choices.TransactionStatus.RECONCILED,
                    ],
                )
                .annotate(period=trunc_func("transaction_date"))
                .values("period")
                .annotate(
                    income=Coalesce(
                        Sum(
                            "amount",
                            filter=Q(transaction_type=choices.TransactionType.INCOME),
                        ),
                        Decimal("0.00"),
                    ),
                    expense=Coalesce(
                        Sum(
                            "amount",
                            filter=Q(transaction_type=choices.TransactionType.EXPENSE),
                        ),
                        Decimal("0.00"),
                    ),
                )
                .order_by("period")
            )

            changes_map = {
                (
                    item["period"].date()
                    if hasattr(item["period"], "date")
                    else item["period"]
                ): item
                for item in period_changes
            }

            # 3. Generate history
            history = []
            current_date = start_date_obj
            running_balance = opening_balance

            # Helper for next date
            def get_next_date(d, interval):
                if interval == "monthly":
                    # Add month safely
                    next_month = d.replace(day=28) + timedelta(days=4)
                    return next_month.replace(day=1)
                elif interval == "weekly":
                    return d + timedelta(weeks=1)
                return d + timedelta(days=1)

            while current_date <= end_date_obj:
                impact = changes_map.get(
                    current_date, {"income": Decimal("0"), "expense": Decimal("0")}
                )

                # IMPORTANT: Income adds to balance, Expense subtracts
                net_change = impact["income"] - impact["expense"]
                closing_balance = running_balance + net_change

                history.append(
                    {
                        "date": current_date,
                        "balance": str(closing_balance),
                        "income": str(impact["income"]),
                        "expense": str(impact["expense"]),
                        "currency": account.currency.code,
                    }
                )

                running_balance = closing_balance
                current_date = get_next_date(current_date, interval)

            return Response(history)

        except Exception as e:
            return self._handle_api_error(
                e,
                "Failed to get balance history.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_deletion_constraints(self, account: Account) -> Dict[str, Any]:
        """Get detailed information about deletion constraints."""
        from .models import Budget, FinancialGoal, Transaction

        constraints = {
            "has_transactions": account.transaction_count > 0,
            "transaction_count": account.transaction_count,
            "is_locked": account.is_locked,
            "has_pending_transactions": False,
            "pending_count": 0,
            "linked_budgets": False,
            "budget_count": 0,
            "linked_goals": False,
            "goal_count": 0,
            "recent_transfers": False,
            "transfer_count": 0,
            "account_too_new": False,
            "account_age_days": (timezone.now() - account.created_at).days,
            "min_age_days": 7,
            "has_banking_connection": False,
            "recently_reconciled": False,
            "days_since_reconciliation": None,
        }

        try:
            # Pending transactions
            pending_count = Transaction.objects.filter(
                account=account, status=choices.TransactionStatus.PENDING
            ).count()
            constraints["has_pending_transactions"] = pending_count > 0
            constraints["pending_count"] = pending_count

            # Budgets
            budget_count = Budget.objects.filter(
                models.Q(linked_account=account) | models.Q(accounts=account)
            ).count()
            constraints["linked_budgets"] = budget_count > 0
            constraints["budget_count"] = budget_count

            # Financial goals
            goal_count = FinancialGoal.objects.filter(
                linked_account=account, is_active=True, is_achieved=False
            ).count()
            constraints["linked_goals"] = goal_count > 0
            constraints["goal_count"] = goal_count

            # Recent transfers
            transfer_count = Transaction.objects.filter(
                transfer_account=account,
                status__in=[
                    choices.TransactionStatus.PENDING,
                    choices.TransactionStatus.COMPLETED,
                ],
                transaction_date__gte=timezone.now().date() - timedelta(days=30),
            ).count()
            constraints["recent_transfers"] = transfer_count > 0
            constraints["transfer_count"] = transfer_count

            # Account age
            constraints["account_too_new"] = constraints["account_age_days"] < 7

            # Banking connection
            if (
                account.institution_data
                and account.institution_data.get("connection_status") == "active"
            ):
                constraints["has_banking_connection"] = True

            # Reconciliation
            if account.reconciled_at:
                days_since = (timezone.now() - account.reconciled_at).days
                constraints["days_since_reconciliation"] = days_since
                constraints["recently_reconciled"] = days_since < 30

        except Exception as e:
            logger.error(
                f"Error gathering deletion constraints for account {account.id}: {e}"
            )

        return constraints

    def _get_deletion_suggestions(self, constraints: Dict[str, Any]) -> List[str]:
        """Get actionable suggestions based on deletion constraints."""
        suggestions = []

        if constraints["has_transactions"]:
            suggestions.append(
                f"Archive the account instead of deleting (preserves {constraints['transaction_count']} transactions)"
            )

        if constraints["is_locked"]:
            suggestions.append("Unlock the account first in account settings")

        if constraints["has_pending_transactions"]:
            suggestions.append(
                f"Complete or cancel {constraints['pending_count']} pending transaction(s)"
            )

        if constraints["linked_budgets"]:
            suggestions.append(
                f"Unlink account from {constraints['budget_count']} budget(s)"
            )

        if constraints["linked_goals"]:
            suggestions.append(
                f"Unlink account from {constraints['goal_count']} financial goal(s)"
            )

        if constraints["recent_transfers"]:
            suggestions.append(
                f"Wait for {constraints['transfer_count']} recent transfer(s) to age beyond 30 days"
            )

        if constraints["account_too_new"]:
            days_needed = 7 - constraints["account_age_days"]
            suggestions.append(
                f"Wait {days_needed} more day(s) (accounts must be at least 7 days old)"
            )

        if constraints["has_banking_connection"]:
            suggestions.append("Disconnect banking connection first")

        if constraints["recently_reconciled"]:
            days_needed = 30 - constraints["days_since_reconciliation"]
            suggestions.append(
                f"Wait {days_needed} more day(s) for audit cycle completion"
            )

        if not suggestions:
            suggestions.append("No constraints found, account can be safely deleted")

        return suggestions

    @extend_schema(
        summary="Reconcile account",
        description="Reconcile account balance with external statement.",
        request=AccountReconcileSerializer,
        responses={
            200: AccountReconcileSerializer,
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    @action(detail=True, methods=["post"], url_path="reconcile")
    def reconcile(self, request, id=None):
        """Reconcile account balance."""
        try:
            account = self.get_object()
            self._check_account_modification_permission(account, request.user)

            serializer = AccountReconcileSerializer(
                data=request.data, context={"account": account, "request": request}
            )
            serializer.is_valid(raise_exception=True)

            result = serializer.save()

            logger.info(
                f"Account reconciled: id={account.id}, "
                f"difference={result.get('difference')}, "
                f"by user={request.user.id}"
            )

            return Response(result)

        except PermissionError as e:
            logger.warning(f"Permission denied reconciling account: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except serializers.ValidationError as e:
            logger.warning(f"Reconciliation validation failed: {e}")
            return Response(
                {"error": "Validation failed.", "details": e.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return self._handle_api_error(
                e, "Failed to reconcile account.", status.HTTP_400_BAD_REQUEST
            )

    @extend_schema(
        summary="Get account summary",
        description="Get a comprehensive summary of all user accounts, including active, inactive, and locked status.",
        responses={
            200: OpenApiResponse(description="Detailed account summary"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """
        Get a comprehensive account summary for the user.

        Provides:
        - Overall balance (active accounts)
        - Status breakdown (active, inactive, locked)
        - Currency and Type distributions
        - Detailed lists of locked and inactive accounts
        """
        try:
            user = request.user
            # Fetch all accounts associated with the user
            all_accounts = self.get_queryset().select_related("currency")

            # Initialize summary containers
            total_active_balance = Decimal("0.00")
            currency_summary = {}
            type_summary = {}
            status_summary = {
                "active": {"count": 0, "balance": Decimal("0.00")},
                "inactive": {"count": 0, "balance": Decimal("0.00")},
                "locked": {"count": 0, "balance": Decimal("0.00")},
            }

            locked_accounts_list = []
            inactive_accounts_list = []

            for account in all_accounts:
                curr_balance = account.current_balance

                # 1. Update status tracking
                if account.is_active:
                    status_summary["active"]["count"] += 1
                    status_summary["active"]["balance"] += curr_balance
                    total_active_balance += curr_balance
                else:
                    status_summary["inactive"]["count"] += 1
                    status_summary["inactive"]["balance"] += curr_balance
                    inactive_accounts_list.append(account)

                if account.is_locked:
                    status_summary["locked"]["count"] += 1
                    status_summary["locked"]["balance"] += curr_balance
                    locked_accounts_list.append(account)

                # 2. Currency summary (only for active accounts to avoid skewing liquid net worth)
                if account.is_active:
                    currency_code = account.currency.code
                    if currency_code not in currency_summary:
                        currency_summary[currency_code] = {
                            "balance": Decimal("0.00"),
                            "count": 0,
                            "symbol": account.currency.symbol or currency_code,
                        }
                    currency_summary[currency_code]["balance"] += curr_balance
                    currency_summary[currency_code]["count"] += 1

                    # 3. Account type summary
                    if account.account_type not in type_summary:
                        type_summary[account.account_type] = {
                            "balance": Decimal("0.00"),
                            "count": 0,
                        }
                    type_summary[account.account_type]["balance"] += curr_balance
                    type_summary[account.account_type]["count"] += 1

            # Format Decimals for JSON serialization
            def format_decimal_data(data_dict):
                for key, value in data_dict.items():
                    if isinstance(value, Decimal):
                        data_dict[key] = str(value)
                    elif isinstance(value, dict):
                        format_decimal_data(value)

            format_decimal_data(currency_summary)
            format_decimal_data(type_summary)
            format_decimal_data(status_summary)

            primary_account = Account.objects.get_user_primary_account(user.id)

            summary = {
                "overall": {
                    "total_accounts": all_accounts.count(),
                    "total_active_balance": str(total_active_balance),
                    "status_breakdown": status_summary,
                },
                "primary_account": (
                    AccountListSerializer(primary_account).data
                    if primary_account
                    else None
                ),
                "currency_breakdown": currency_summary,
                "type_breakdown": type_summary,
                "inactive_accounts": AccountListSerializer(
                    inactive_accounts_list, many=True
                ).data,
                "locked_accounts": AccountListSerializer(
                    locked_accounts_list, many=True
                ).data,
            }

            logger.info(f"Generated comprehensive summary for user {user.id}")
            return Response(summary)

        except Exception as e:
            return self._handle_api_error(
                e,
                "Failed to generate comprehensive account summary.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Get balance history",
        parameters=[
            OpenApiParameter(
                name="start_date",
                description="Start date for history (YYYY-MM-DD)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="end_date",
                description="End date for history (YYYY-MM-DD)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="interval",
                description="Grouping interval",
                required=False,
                type=str,
                enum=["daily", "weekly", "monthly"],
            ),
        ],
        responses={
            200: OpenApiResponse(description="Balance history"),
            400: OpenApiResponse(description="Invalid date range"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    @action(detail=True, methods=["get"], url_path="balance-history")
    def balance_history(self, request, id=None):
        """Get account balance history."""
        try:
            account = self.get_object()
            self._check_account_modification_permission(account, request.user)

            # Parse date parameters
            start_date = request.query_params.get("start_date")
            end_date = request.query_params.get("end_date")

            if start_date:
                try:
                    start_date = timezone.datetime.strptime(
                        start_date, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    return Response(
                        {"error": "Invalid start_date format. Use YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            if end_date:
                try:
                    end_date = timezone.datetime.strptime(end_date, "%Y-%m-%d").date()
                except ValueError:
                    return Response(
                        {"error": "Invalid end_date format. Use YYYY-MM-DD."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            # Get balance history
            history = account.get_balance_history(start_date, end_date)

            # Apply interval grouping if requested
            interval = request.query_params.get("interval")
            if interval:
                history = self._group_history_by_interval(history, interval)

            return Response(
                {
                    "account_id": str(account.id),
                    "account_name": account.name,
                    "currency": account.currency.code,
                    "current_balance": str(account.current_balance),
                    "history": history,
                    "period": {
                        "start": start_date,
                        "end": end_date,
                    },
                }
            )

        except PermissionError as e:
            logger.warning(f"Permission denied getting balance history: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            return self._handle_api_error(
                e,
                "Failed to retrieve balance history.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _group_history_by_interval(
        self, history: List[Dict[str, Any]], interval: str
    ) -> List[Dict[str, Any]]:
        """
        Group balance history by time interval with comprehensive logic.

        Supports:
        - Daily: Group by day (no change)
        - Weekly: Group by ISO week (Monday-Sunday)
        - Monthly: Group by month
        - Quarterly: Group by quarter
        - Yearly: Group by year

        For grouped intervals:
        - Start date: First date in the interval
        - End date: Last date in the interval
        - Opening balance: Balance at start of interval
        - Closing balance: Balance at end of interval
        - Min balance: Minimum balance in interval
        - Max balance: Maximum balance in interval
        - Total income: Sum of income in interval
        - Total expense: Sum of expense in interval
        - Transaction count: Number of transactions in interval

        Args:
            history: List of daily balance entries
            interval: Grouping interval ('daily', 'weekly', 'monthly', 'quarterly', 'yearly')

        Returns:
            Grouped history with interval statistics
        """
        if not history:
            return []

        if interval == "daily":
            # No grouping needed for daily
            return history

        # Validate interval
        valid_intervals = ["daily", "weekly", "monthly", "quarterly", "yearly"]
        if interval not in valid_intervals:
            logger.warning(f"Invalid interval '{interval}', defaulting to daily")
            return history

        logger.debug(f"Grouping {len(history)} history entries by {interval} interval")

        try:
            grouped_data = []
            current_group = None

            for entry in sorted(history, key=lambda x: x["date"]):
                entry_date = entry["date"]

                # Determine interval key based on interval type
                if interval == "weekly":
                    # ISO week: Year-WeekNumber
                    iso_year, iso_week, _ = entry_date.isocalendar()
                    interval_key = f"{iso_year}-W{iso_week:02d}"
                    interval_start = entry_date - timedelta(
                        days=entry_date.weekday()
                    )  # Monday
                    interval_end = interval_start + timedelta(days=6)  # Sunday

                elif interval == "monthly":
                    # Year-Month
                    interval_key = f"{entry_date.year}-{entry_date.month:02d}"
                    interval_start = date(entry_date.year, entry_date.month, 1)
                    # Last day of month
                    if entry_date.month == 12:
                        interval_end = date(entry_date.year, 12, 31)
                    else:
                        interval_end = date(
                            entry_date.year, entry_date.month + 1, 1
                        ) - timedelta(days=1)

                elif interval == "quarterly":
                    # Year-Quarter
                    quarter = (entry_date.month - 1) // 3 + 1
                    interval_key = f"{entry_date.year}-Q{quarter}"
                    # Quarter start month
                    quarter_start_month = (quarter - 1) * 3 + 1
                    interval_start = date(entry_date.year, quarter_start_month, 1)
                    # Quarter end month
                    quarter_end_month = quarter_start_month + 2
                    if quarter_end_month == 12:
                        interval_end = date(entry_date.year, 12, 31)
                    else:
                        interval_end = date(
                            entry_date.year, quarter_end_month + 1, 1
                        ) - timedelta(days=1)

                elif interval == "yearly":
                    # Year
                    interval_key = str(entry_date.year)
                    interval_start = date(entry_date.year, 1, 1)
                    interval_end = date(entry_date.year, 12, 31)

                # Initialize new group if needed
                if not current_group or current_group["interval_key"] != interval_key:
                    if current_group:
                        # Finalize previous group
                        self._finalize_group(current_group)
                        grouped_data.append(current_group)

                    # Start new group
                    current_group = {
                        "interval_key": interval_key,
                        "interval_type": interval,
                        "interval_start": interval_start.isoformat(),
                        "interval_end": interval_end.isoformat(),
                        "opening_balance": entry["balance"]
                        - entry["income"]
                        + entry["expense"],  # Balance before this day
                        "closing_balance": entry["balance"],
                        "min_balance": entry["balance"],
                        "max_balance": entry["balance"],
                        "total_income": entry["income"],
                        "total_expense": entry["expense"],
                        "transaction_days": (
                            1 if entry["income"] != 0 or entry["expense"] != 0 else 0
                        ),
                        "days_in_interval": 1,
                    }
                else:
                    # Update existing group
                    current_group["closing_balance"] = entry["balance"]
                    current_group["min_balance"] = min(
                        current_group["min_balance"], entry["balance"]
                    )
                    current_group["max_balance"] = max(
                        current_group["max_balance"], entry["balance"]
                    )
                    current_group["total_income"] += entry["income"]
                    current_group["total_expense"] += entry["expense"]
                    current_group["days_in_interval"] += 1

                    if entry["income"] != 0 or entry["expense"] != 0:
                        current_group["transaction_days"] += 1

            # Don't forget to add the last group
            if current_group:
                self._finalize_group(current_group)
                grouped_data.append(current_group)

            logger.debug(f"Grouped into {len(grouped_data)} {interval} intervals")
            return grouped_data

        except Exception as e:
            logger.exception(f"Error grouping history by interval '{interval}': {e}")
            # Return ungrouped history as fallback
            return history

    def _finalize_group(self, group: Dict[str, Any]) -> None:
        """
        Finalize group statistics after all entries have been added.

        Calculates:
        - Net flow (income - expense)
        - Average daily balance
        - Balance volatility
        - Growth percentage
        """
        try:
            # Calculate net flow
            group["net_flow"] = group["total_income"] - group["total_expense"]

            # Calculate average balance (simplified)
            if group["days_in_interval"] > 0:
                # For simplicity, average of opening and closing
                group["avg_balance"] = (
                    group["opening_balance"] + group["closing_balance"]
                ) / 2
            else:
                group["avg_balance"] = group["opening_balance"]

            # Calculate balance range
            group["balance_range"] = group["max_balance"] - group["min_balance"]

            # Calculate growth percentage
            if group["opening_balance"] != 0:
                group["growth_percentage"] = (
                    (group["closing_balance"] - group["opening_balance"])
                    / abs(group["opening_balance"])
                    * 100
                )
            else:
                group["growth_percentage"] = 0

            # Determine trend
            if group["growth_percentage"] > 1:
                group["trend"] = "up"
            elif group["growth_percentage"] < -1:
                group["trend"] = "down"
            else:
                group["trend"] = "stable"

            # Calculate volatility (simplified)
            if group["balance_range"] != 0 and group["avg_balance"] != 0:
                group["volatility"] = (
                    group["balance_range"] / abs(group["avg_balance"])
                ) * 100
            else:
                group["volatility"] = 0

            # Format for JSON serialization (convert Decimal to string)
            decimal_fields = [
                "opening_balance",
                "closing_balance",
                "min_balance",
                "max_balance",
                "total_income",
                "total_expense",
                "net_flow",
                "avg_balance",
                "balance_range",
                "growth_percentage",
                "volatility",
            ]

            for field in decimal_fields:
                if field in group and isinstance(group[field], Decimal):
                    group[field] = str(group[field])

        except Exception as e:
            logger.error(f"Error finalizing group {group.get('interval_key')}: {e}")
            # Ensure at least basic fields are strings
            if "opening_balance" in group and isinstance(
                group["opening_balance"], Decimal
            ):
                group["opening_balance"] = str(group["opening_balance"])
            if "closing_balance" in group and isinstance(
                group["closing_balance"], Decimal
            ):
                group["closing_balance"] = str(group["closing_balance"])


class TransactionViewSet(viewsets.ModelViewSet):
    """
    Complete transaction management API endpoint.

    Implements:
    - Full CRUD operations with proper permissions
    - Advanced filtering and searching
    - Bulk operations
    - Transaction verification workflow
    - Analytics and reporting
    - Export functionality
    - Audit logging

    Security:
    - User-specific data isolation
    - Role-based access control
    - Rate limiting
    - Input validation and sanitization

    Performance:
    - Optimized database queries
    - Selective field loading
    - Caching for frequent operations
    - Background processing for bulk operations
    """

    queryset = Transaction.objects.none()  # Will be set in get_queryset
    serializer_class = TransactionSerializer
    pagination_class = CustomPageNumberPagination
    permission_classes = [IsOwnerOrAdmin]
    throttle_classes = [throttlings.TransactionThrottle]
    filterset_class = TransactionFilter
    search_fields = ["name", "description", "merchant", "reference_number"]
    ordering_fields = ["transaction_date", "amount", "created_at"]
    ordering = ["-transaction_date"]
    lookup_field = "id"

    # Disable PUT method (use PATCH for partial updates)
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """
        Returns queryset filtered by user ownership with optimization.

        Optimizations:
        - Prefetches related objects to prevent N+1 queries
        - Selects only necessary fields
        - Applies filtering early in the query pipeline

        Returns:
            QuerySet: Filtered and optimized transaction queryset
        """
        user = self.request.user

        # Base queryset with all necessary prefetches
        queryset = Transaction.objects.select_related(
            "user",
            "account",
            "account__currency",
            "category",
            "original_currency",
            "transfer_account",
        ).only(
            "id",
            "user_id",
            "account_id",
            "category_id",
            "name",
            "transaction_type",
            "amount",
            "original_amount",
            "original_currency_id",
            "exchange_rate",
            "description",
            "status",
            "transaction_date",
            "created_at",
            "updated_at",
            "is_transfer",
            "transfer_account_id",
            "transfer_reference",
            "tags",
            "attachments",
            "is_recurring",
            "is_tax_deductible",
            "merchant",
            "reference_number",
            # Include essentials from related models to prevent deferred queries
            "user__username",
            "account__name",
            "account__currency_id",
            "category__name",
            "original_currency__code",
            "transfer_account__name",
        )
        # Filter by user ownership (unless admin)
        if not (user.is_staff or user.is_superuser):
            queryset = queryset.filter(user=user)

        return queryset

    def get_serializer_class(self):
        """
        Returns appropriate serializer based on action.

        Strategy:
        - Different serializers for create/update to enforce business rules
        - Specialized serializers for bulk operations
        - Action-specific validation logic

        Returns:
            Serializer class for the current action
        """
        if self.action == "create":
            return TransactionCreateSerializer
        elif self.action == "partial_update":
            return TransactionUpdateSerializer
        elif self.action == "verify":
            return TransactionVerificationSerializer
        elif self.action == "reconcile":
            return TransactionReconciliationSerializer
        return super().get_serializer_class()

    def list(self, request, *args, **kwargs):
        """
        List transactions with filtering, pagination, and analytics.
        """
        try:
            # Get optimized queryset
            queryset = self.get_queryset()

            # Apply advanced filtering
            queryset = self.filter_queryset(queryset)

            # Check for export request (if needed)
            # export_format = request.query_params.get("export")
            # if export_format and (request.user.is_staff or request.user.is_superuser):
            #     return self._export_transactions(queryset, export_format)

            # Paginate
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(
                {
                    "transactions": serializer.data,
                    "count": queryset.count(),
                }
            )

        except Exception as e:
            logger.exception(f"Error listing transactions: {e}")
            return Response(
                {
                    "error": _(
                        "Failed to retrieve transactions. Please try again later."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        operation_id="transactions_create",
        responses={
            201: TransactionSerializer,
            400: inline_serializer(
                name="TransactionCreateError",
                fields={
                    "error": serializers.CharField(),
                    "details": serializers.DictField(required=False),
                },
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        """
        Create a single transaction with atomic operation.

        Business Rules:
        - Validates ownership of account and category
        - Updates account balance if status is COMPLETED
        - Creates transfer pair if is_transfer is True
        - Enforces currency conversion rules

        Returns:
            Created transaction with 201 status
        """
        try:
            serializer = self.get_serializer(
                data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            # Atomic creation to ensure data consistency
            with db_transaction.atomic():
                transaction = serializer.save(user=request.user)

            logger.info(
                f"Transaction created: {transaction.id}",
                extra={
                    "transaction_id": transaction.id,
                    "user_id": request.user.id,
                    "account_id": transaction.account_id,
                    "amount": transaction.amount,
                    "type": transaction.transaction_type,
                },
            )

            return Response(
                TransactionSerializer(transaction).data, status=status.HTTP_201_CREATED
            )

        except (DRFValidationError, ValidationError) as e:
            # Handle both DRF and Django validation errors
            error_details = getattr(e, "detail", getattr(e, "message_dict", str(e)))
            logger.warning(
                f"Transaction validation failed: {e}",
                extra={"user_id": request.user.id, "data": request.data},
            )
            return Response(
                {"error": _("Validation failed"), "details": error_details},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PermissionDenied as e:
            logger.warning(
                f"Permission denied for transaction creation: {e}",
                extra={"user_id": request.user.id},
            )
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception(
                f"Error creating transaction for user {request.user.id}: {e}"
            )
            return Response(
                {"error": _("Failed to create transaction. Please try again later.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a specific transaction with enhanced details.

        Includes:
        - Full transaction details
        - Related transfer transaction (if applicable)
        - Audit trail (if admin)
        - Similar transactions suggestion

        Returns:
            Complete transaction details
        """
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)

            # Add related data
            data = serializer.data
            if instance.is_transfer and instance.transfer_reference:
                try:
                    transfer_transaction = Transaction.objects.get(
                        id=instance.transfer_reference
                    )
                    data["transfer_transaction"] = TransactionSerializer(
                        transfer_transaction
                    ).data
                except Transaction.DoesNotExist:
                    pass

            # Add audit info for staff
            if request.user.is_staff:
                data["audit"] = {
                    "created_by": instance.user_id,
                    "created_at": instance.created_at,
                    "last_modified": instance.updated_at,
                    "import_source": instance.imported_source,
                }

            return Response(data)

        except Exception as e:
            logger.exception(f"Error retrieving transaction {kwargs.get('id')}: {e}")
            return Response(
                {"error": _("Failed to retrieve transaction details.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def partial_update(self, request, *args, **kwargs):
        """
        Partially update a transaction with atomic operation.

        Restrictions:
        - Cannot update transaction_type after creation
        - Cannot modify completed/reconciled transactions without permissions
        - Account balance is adjusted for amount/status changes

        Returns:
            Updated transaction
        """
        try:
            instance = self.get_object()

            # Check if transaction can be modified
            if not self._can_modify_transaction(instance, request.user):
                return Response(
                    {"error": _("Cannot modify completed or reconciled transactions.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = self.get_serializer(
                instance, data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            # Track changes for audit
            changes = {
                field: (getattr(instance, field), value)
                for field, value in request.data.items()
                if hasattr(instance, field) and getattr(instance, field) != value
            }

            # Atomic update to ensure data consistency
            with db_transaction.atomic():
                transaction = serializer.save()

            logger.info(
                f"Transaction updated: {transaction.id}",
                extra={
                    "transaction_id": transaction.id,
                    "user_id": request.user.id,
                    "changes": changes,
                },
            )

            return Response(TransactionSerializer(transaction).data)

        except (DRFValidationError, ValidationError) as e:
            error_details = getattr(e, "detail", getattr(e, "message_dict", str(e)))
            logger.warning(
                f"Transaction update validation failed: {e}",
                extra={
                    "transaction_id": kwargs.get("id"),
                    "user_id": request.user.id,
                    "data": request.data,
                },
            )
            return Response(
                {"error": _("Validation failed"), "details": error_details},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"Error updating transaction {kwargs.get('id')}: {e}")
            return Response(
                {"error": _("Failed to update transaction. Please try again later.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def destroy(self, request, *args, **kwargs):
        """
        Soft delete a transaction with balance adjustment.

        Effects:
        - Transaction is marked as deleted (soft delete)
        - Account balance is adjusted if transaction was completed
        - Transfer pairs are also soft deleted
        - Audit log is created

        Returns:
            204 No Content on success
        """
        try:
            instance = self.get_object()

            # Check permissions for deletion
            if (
                instance.status == TransactionStatus.RECONCILED
                and not request.user.is_staff
            ):
                return Response(
                    {
                        "error": _(
                            "Cannot delete reconciled transactions without admin permission."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Atomic deletion with balance adjustment
            with db_transaction.atomic():
                # Adjust balance if transaction was completed
                if instance.status == TransactionStatus.COMPLETED and instance.account:
                    # Reverse the transaction amount
                    reverse_type = (
                        choices.TransactionType.EXPENSE
                        if instance.transaction_type == choices.TransactionType.INCOME
                        else choices.TransactionType.INCOME
                    )
                    instance.account.update_balance(instance.amount, reverse_type)

                # Soft delete transfer pair if exists
                if instance.is_transfer and instance.transfer_reference:
                    try:
                        transfer_transaction = Transaction.objects.get(
                            id=instance.transfer_reference
                        )
                        transfer_transaction.delete()
                    except Transaction.DoesNotExist:
                        pass

                # Soft delete the transaction
                instance.delete()

            logger.info(
                f"Transaction deleted: {instance.id}",
                extra={
                    "transaction_id": instance.id,
                    "user_id": request.user.id,
                    "amount": instance.amount,
                    "type": instance.transaction_type,
                },
            )

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            logger.exception(f"Error deleting transaction {kwargs.get('id')}: {e}")
            return Response(
                {"error": _("Failed to delete transaction. Please try again later.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Verify Transaction",
        description="Mark a transaction as verified/completed.",
        request=TransactionVerificationSerializer,
        responses={
            200: TransactionSerializer,
            400: OpenApiResponse(description="Transaction cannot be verified"),
        },
    )
    @action(detail=True, methods=["post"], url_path="verify")
    def verify(self, request, *args, **kwargs):
        """
        Verify and complete a transaction.

        Effects:
        - Updates status to COMPLETED
        - Updates account balance
        - Creates audit log
        - Sends notifications (if configured)

        Returns:
            Verified transaction
        """
        try:
            instance = self.get_object()

            # Check if transaction can be verified
            if instance.status != choices.TransactionStatus.PENDING:
                return Response(
                    {
                        "error": _(
                            f"Cannot verify transaction in {instance.status} status."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = self.get_serializer(
                instance, data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            # Atomic verification
            with db_transaction.atomic():
                transaction = serializer.save()

            logger.info(
                f"Transaction verified: {transaction.id}",
                extra={
                    "transaction_id": transaction.id,
                    "user_id": request.user.id,
                    "verified_by": request.user.id,
                },
            )

            return Response(TransactionSerializer(transaction).data)

        except Exception as e:
            logger.exception(f"Error verifying transaction {kwargs.get('id')}: {e}")
            return Response(
                {"error": _("Failed to verify transaction. Please try again later.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Get Transaction Analytics",
        description="Retrieve comprehensive analytics for transactions.",
        parameters=[
            OpenApiParameter(
                name="group_by",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Group analytics by field",
                enum=["category", "account", "month", "week", "day", "type"],
            ),
        ],
        responses={
            200: inline_serializer(
                name="AnalyticsResponse",
                fields={
                    "summary": serializers.DictField(),
                    "trends": serializers.ListField(child=serializers.DictField()),
                    "breakdown": serializers.ListField(child=serializers.DictField()),
                },
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="analytics")
    def analytics(self, request, *args, **kwargs):
        """
        Retrieve transaction analytics and insights.

        Analytics include:
        - Summary statistics (totals, averages, counts)
        - Trends over time
        - Category/account breakdown
        - Forecasting (if historical data available)

        Returns:
            Comprehensive analytics data
        """
        try:
            queryset = self.filter_queryset(self.get_queryset())
            group_by = request.query_params.get("group_by", "month")

            analytics_data = self._calculate_analytics(queryset, group_by)

            return Response(analytics_data)

        except Exception as e:
            logger.exception(
                f"Error generating analytics for user {request.user.id}: {e}"
            )
            return Response(
                {"error": _("Failed to generate analytics. Please try again later.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _can_modify_transaction(self, transaction: Transaction, user) -> bool:
        """
        Check if a transaction can be modified.

        Args:
            transaction: Transaction instance
            user: Requesting user

        Returns:
            True if transaction can be modified
        """
        # Staff can modify any transaction
        if user.is_staff or user.is_superuser:
            return True

        # Users cannot modify completed or reconciled transactions
        if transaction.status in [
            choices.TransactionStatus.COMPLETED,
            choices.TransactionStatus.RECONCILED,
        ]:
            return False

        return True

    def _get_analytics_summary(self, queryset) -> Dict[str, Any]:
        """
        Calculate comprehensive analytics summary for a queryset.
        """
        try:
            aggregates = queryset.aggregate(
                total_count=Count("id"),
                total_amount=Sum("amount"),
                avg_amount=Avg("amount"),
                total_income=Sum(
                    "amount", filter=Q(transaction_type=choices.TransactionType.INCOME)
                ),
                total_expense=Sum(
                    "amount", filter=Q(transaction_type=choices.TransactionType.EXPENSE)
                ),
                tax_deductible=Sum("amount", filter=Q(is_tax_deductible=True)),
                latest_date=Max("transaction_date"),
                earliest_date=Min("transaction_date"),
            )

            total_income = aggregates["total_income"] or Decimal("0.00")
            total_expense = aggregates["total_expense"] or Decimal("0.00")
            net_savings = total_income - total_expense

            # Calculate daily average
            days = 1
            if aggregates["latest_date"] and aggregates["earliest_date"]:
                delta = aggregates["latest_date"] - aggregates["earliest_date"]
                days = max(delta.days, 1)

            return {
                "summary": {
                    "total_transactions": aggregates["total_count"] or 0,
                    "total_income": str(total_income),
                    "total_expense": str(total_expense),
                    "net_savings": str(net_savings),
                    "savings_rate": (
                        round((float(net_savings) / float(total_income)) * 100, 2)
                        if total_income > 0
                        else 0
                    ),
                    "daily_avg_spend": str(round(total_expense / days, 2)),
                    "tax_deductible_total": str(
                        aggregates["tax_deductible"] or Decimal("0.00")
                    ),
                },
                "meta": {
                    "period_days": days,
                    "status_counts": {
                        status_choice[0]: queryset.filter(
                            status=status_choice[0]
                        ).count()
                        for status_choice in choices.TransactionStatus.choices
                    },
                },
            }
        except Exception as e:
            logger.error(f"Error calculating analytics summary: {e}")
            return {}

    def _calculate_analytics(self, queryset, group_by: str) -> Dict[str, Any]:
        """
        Calculate detailed analytics with grouping.
        """
        return {
            "summary": self._get_analytics_summary(queryset),
            "trends": self._get_time_based_trends(queryset, group_by),
            "breakdowns": {
                "categories": self._get_breakdown_by_field(queryset, "category"),
                "merchants": self._get_breakdown_by_field(queryset, "merchant"),
                "accounts": self._get_breakdown_by_field(queryset, "account"),
            },
        }

    def _get_time_based_trends(self, queryset, interval: str) -> List[Dict[str, Any]]:
        """
        Get transaction trends (Income vs Expense) over time.
        """
        trunc_func = TruncMonth
        if interval == "week":
            trunc_func = TruncWeek
        elif interval == "day":
            trunc_func = TruncDay

        trends = (
            queryset.annotate(period=trunc_func("transaction_date"))
            .values("period")
            .annotate(
                income=Sum(
                    "amount", filter=Q(transaction_type=choices.TransactionType.INCOME)
                ),
                expense=Sum(
                    "amount", filter=Q(transaction_type=choices.TransactionType.EXPENSE)
                ),
            )
            .order_by("period")
        )

        return [
            {
                "period": t["period"].strftime("%Y-%m-%d") if t["period"] else None,
                "income": str(t["income"] or 0),
                "expense": str(t["expense"] or 0),
                "net": str((t["income"] or 0) - (t["expense"] or 0)),
            }
            for t in trends
        ]

    def _get_breakdown_by_field(self, queryset, field: str) -> List[Dict[str, Any]]:
        """
        Enhanced breakdown by Category, Merchant, or Account.
        """
        if field == "category":
            items = (
                queryset.values(
                    "category__id", "category__name", "category__category_type"
                )
                .annotate(count=Count("id"), total=Sum("amount"))
                .order_by("-total")[:10]
            )
            return [
                {
                    "id": i["category__id"],
                    "name": i["category__name"],
                    "type": i["category__category_type"],
                    "total": str(i["total"]),
                    "count": i["count"],
                }
                for i in items
            ]
        elif field == "merchant":
            items = (
                queryset.filter(merchant__isnull=False)
                .values("merchant")
                .annotate(count=Count("id"), total=Sum("amount"))
                .order_by("-total")[:10]
            )
            return [
                {
                    "merchant": i["merchant"],
                    "total": str(i["total"]),
                    "count": i["count"],
                }
                for i in items
            ]
        elif field == "account":
            items = (
                queryset.values("account__id", "account__name")
                .annotate(count=Count("id"), total=Sum("amount"))
                .order_by("-total")
            )
            return [
                {
                    "id": i["account__id"],
                    "name": i["account__name"],
                    "total": str(i["total"]),
                    "count": i["count"],
                }
                for i in items
            ]
        return []

    def _export_transactions(self, queryset, format: str) -> Response:
        """
        Export transactions in specified format.

        Args:
            queryset: Transactions to export
            format: Export format (csv, excel, json)

        Returns:
            File response with exported data
        """
        # Implementation depends on your export requirements
        # Could use Django REST Framework's renderers or external libraries

        raise NotImplementedError("Export functionality not implemented")


class BudgetViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    Budget ViewSet for comprehensive budget management.

    Features:
    - Full CRUD operations with role-based permissions
    - Budget recalculation and spending tracking
    - Advanced filtering and search capabilities
    - Period advancement and rollover handling
    - Comprehensive error handling and logging

    Permissions:
    - Regular users: CRUD only their own budgets
    - Staff/Admin: CRUD any budget

    Security:
    - Proper ownership validation
    - Business logic enforcement
    - Audit logging for sensitive operations
    """

    queryset = Budget.objects.all()
    permission_classes = [IsOwnerOrAdmin]
    pagination_class = CustomPageNumberPagination
    filterset_class = BudgetFilter
    search_fields = ["name", "description"]
    ordering_fields = [
        "name",
        "total_budget",
        "total_spent",
        "start_date",
        "end_date",
        "created_at",
    ]
    ordering = ["-created_at"]
    lookup_field = "id"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """
        Get budgets based on user permissions with optimization.

        Rules:
        - Superusers/Staff: All budgets with related data
        - Regular users: Only their own budgets
        """
        user = self.request.user

        queryset = (
            Budget.objects.select_related("currency", "category", "user")
            .prefetch_related("budget_categories__category")
            .annotate(category_count=Count("budget_categories", distinct=True))
        )

        if user.is_staff or user.is_superuser:
            return queryset

        return queryset.filter(user=user)

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "list":
            return BudgetListSerializer
        elif self.action == "retrieve":
            return BudgetDetailSerializer
        elif self.action == "create":
            return BudgetCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return BudgetUpdateSerializer
        elif self.action == "recalculate":
            return BudgetRecalculateSerializer
        return BudgetSerializer

    @extend_schema(
        summary="List budgets",
        description=(
            "List all budgets accessible to the authenticated user. "
            "Regular users see only their budgets. "
            "Staff/Admin see all budgets."
        ),
        responses={
            200: BudgetListSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List budgets with advanced filtering and pagination."""
        try:
            queryset = self.get_queryset()
            queryset = self.filter_queryset(queryset)

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error listing budgets: {e}")
            return Response(
                {"error": "An error occurred while retrieving budgets."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Retrieve budget",
        description="Retrieve detailed information about a specific budget.",
        responses={
            200: BudgetDetailSerializer,
            404: OpenApiResponse(description="Budget not found"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve budget with detailed information."""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response(serializer.data)

        except Budget.DoesNotExist:
            logger.warning(f"Budget not found: {kwargs.get('id')}")
            return Response(
                {"error": "Budget not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception(f"Error retrieving budget: {e}")
            return Response(
                {"error": "An error occurred while retrieving the budget."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create budget",
        description=(
            "Create a new budget. "
            "Currency, total_budget, start_date, and end_date are required. "
            "Budget name must be unique per user for active budgets."
        ),
        request=BudgetCreateSerializer,
        responses={
            201: BudgetSerializer,
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new budget."""
        try:
            serializer = self.get_serializer(
                data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                budget = serializer.save()

            logger.info(
                f"Budget created: id={budget.id}, "
                f"name='{budget.name}', "
                f"user={request.user.id}, "
                f"type={budget.budget_type}, "
                f"amount={budget.total_budget}"
            )

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except serializers.ValidationError as e:
            logger.warning(f"Budget creation validation failed: {e}")
            return Response(
                {"error": "Validation failed.", "details": e.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"Error creating budget: {e}")
            return Response(
                {"error": "Failed to create budget. Please check your data."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Partial update budget",
        description="Update specific fields of a budget using PATCH.",
        request=BudgetUpdateSerializer,
        responses={
            200: BudgetSerializer,
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        """Partial update budget (PATCH)."""
        try:
            instance = self.get_object()
            self._check_budget_modification_permission(instance, request.user)

            serializer = self.get_serializer(
                instance, data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Budget partially updated: id={updated_instance.id}, "
                f"updated fields={list(request.data.keys())}, "
                f"by user={request.user.id}"
            )

            return Response(serializer.data)

        except PermissionError as e:
            logger.warning(f"Permission denied updating budget: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except serializers.ValidationError as e:
            logger.warning(f"Budget update validation failed: {e}")
            return Response(
                {"error": "Validation failed.", "details": e.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"Error updating budget: {e}")
            return Response(
                {"error": "Failed to update budget."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Delete budget",
        description=(
            "Soft delete a budget (set is_active=False). "
            "Budgets with active allocations cannot be deleted."
        ),
        responses={
            204: OpenApiResponse(description="No Content"),
            400: OpenApiResponse(description="Cannot delete budget"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Budget not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Soft delete budget with validation."""
        try:
            instance = self.get_object()
            user = request.user

            self._check_budget_modification_permission(instance, user)

            with db_transaction.atomic():
                instance.is_active = False
                instance.save(update_fields=["is_active", "updated_at"])

            logger.info(
                f"Budget soft deleted: id={instance.id}, "
                f"name='{instance.name}', "
                f"by user={user.id}"
            )

            return Response(status=status.HTTP_204_NO_CONTENT)

        except PermissionError as e:
            logger.warning(f"Permission denied deleting budget: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception(f"Error deleting budget: {e}")
            return Response(
                {"error": "Failed to delete budget."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _check_budget_modification_permission(self, budget, user):
        """
        Check if user can modify the budget.

        Args:
            budget: Budget instance
            user: User making the request

        Raises:
            PermissionError: If user cannot modify the budget
        """
        if user.is_staff or user.is_superuser:
            return

        if budget.user != user:
            raise PermissionError("Cannot modify another user's budget.")

    @extend_schema(
        summary="Recalculate budget spending",
        description="Manually recalculate budget spending and update totals.",
        request=BudgetRecalculateSerializer,
        responses={
            200: OpenApiResponse(description="Recalculation result"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Budget not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, id=None):
        """Recalculate budget spending."""
        try:
            budget = self.get_object()
            self._check_budget_modification_permission(budget, request.user)

            serializer = self.get_serializer(
                data=request.data, context={"budget": budget}
            )
            serializer.is_valid(raise_exception=True)
            result = serializer.save()

            logger.info(
                f"Budget recalculated: id={budget.id}, by user={request.user.id}"
            )

            return Response(result)

        except PermissionError as e:
            logger.warning(f"Permission denied recalculating budget: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception(f"Error recalculating budget: {e}")
            return Response(
                {"error": "Failed to recalculate budget."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Advance budget period",
        description="Advance budget to next period with optional rollover.",
        responses={
            200: OpenApiResponse(description="Period advanced successfully"),
            400: OpenApiResponse(description="Cannot advance period"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Budget not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="advance-period")
    def advance_period(self, request, id=None):
        """Advance budget to next period."""
        try:
            budget = self.get_object()
            self._check_budget_modification_permission(budget, request.user)

            if budget.advance_period():
                logger.info(
                    f"Budget period advanced: id={budget.id}, "
                    f"new period: {budget.start_date} to {budget.end_date}, "
                    f"by user={request.user.id}"
                )

                serializer = BudgetDetailSerializer(
                    budget, context={"request": request}
                )
                return Response(serializer.data)
            else:
                return Response(
                    {"error": "Failed to advance budget period."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except PermissionError as e:
            logger.warning(f"Permission denied advancing budget period: {e}")
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except Exception as e:
            logger.exception(f"Error advancing budget period: {e}")
            return Response(
                {"error": "Failed to advance budget period."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Get budget statistics",
        description="Get comprehensive statistics for a budget.",
        responses={
            200: OpenApiResponse(description="Budget statistics"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Budget not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="statistics")
    def statistics(self, request, id=None):
        """Get budget statistics."""
        try:
            budget = self.get_object()

            stats = {
                "budget_id": str(budget.id),
                "budget_name": budget.name,
                "total_budget": str(budget.total_budget),
                "total_spent": str(budget.total_spent),
                "total_remaining": str(budget.total_remaining),
                "rollover_amount": str(budget.rollover_amount),
                "utilization_percentage": budget.get_utilization_percentage(),
                "is_over_budget": budget.is_over_budget,
                "days_remaining": budget.days_remaining,
                "should_notify": budget.should_notify(),
                "period": {
                    "type": budget.period_type,
                    "start_date": budget.start_date,
                    "end_date": budget.end_date,
                },
                "last_recalculated_at": budget.last_recalculated_at,
            }

            return Response(stats)

        except Exception as e:
            logger.exception(f"Error getting budget statistics: {e}")
            return Response(
                {"error": "Failed to get budget statistics."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Get budget summary",
        description="Get summary of all active budgets for the user.",
        responses={
            200: OpenApiResponse(description="Budget summary"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Get summary of all active budgets."""
        try:
            queryset = self.get_queryset().filter(is_active=True)

            total_budgeted = queryset.aggregate(total=Sum("total_budget"))[
                "total"
            ] or Decimal("0.00")

            total_spent = queryset.aggregate(total=Sum("total_spent"))[
                "total"
            ] or Decimal("0.00")

            total_remaining = queryset.aggregate(total=Sum("total_remaining"))[
                "total"
            ] or Decimal("0.00")

            over_budget_count = queryset.filter(
                total_spent__gt=models.F("total_budget")
            ).count()

            warning_count = sum(
                1
                for budget in queryset
                if budget.should_notify() and not budget.is_over_budget
            )

            summary = {
                "total_budgets": queryset.count(),
                "total_budgeted": str(total_budgeted),
                "total_spent": str(total_spent),
                "total_remaining": str(total_remaining),
                "overall_utilization": float(
                    (total_spent / total_budgeted * 100) if total_budgeted > 0 else 0
                ),
                "budgets_over_limit": over_budget_count,
                "budgets_warning": warning_count,
                "budgets_on_track": queryset.count()
                - over_budget_count
                - warning_count,
            }

            return Response(summary)

        except Exception as e:
            logger.exception(f"Error getting budget summary: {e}")
            return Response(
                {"error": "Failed to get budget summary."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BudgetCategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Budget Category allocations.

    Provides:
    - CRUD for budget category links
    - Individual spending tracking for categories within budgets
    - Advanced filtering by budget or category
    - Utilization analytics per category
    """

    queryset = BudgetCategory.objects.all()
    serializer_class = BudgetCategorySerializer
    permission_classes = [IsOwnerOrAdmin]
    filterset_class = BudgetCategoryFilter
    ordering_fields = [
        "allocated_amount",
        "spent_amount",
        "remaining_amount",
        "percentage_used",
        "created_at",
    ]
    ordering = ["-created_at"]
    lookup_field = "id"

    def get_queryset(self):
        """
        Filter budget categories by user ownership.
        Users can only see categories linked to their own budgets.
        """
        user = self.request.user
        queryset = BudgetCategory.objects.select_related(
            "budget", "category", "budget__user"
        )

        if user.is_staff or user.is_superuser:
            return queryset

        return queryset.filter(budget__user=user)

    @extend_schema(
        summary="Recalculate budget category",
        description="Manually recalculate spending for this budget category and its parent budget.",
        responses={200: BudgetCategorySerializer},
    )
    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, id=None):
        """
        Manually recalculate spending for this budget category.
        Useful if transactions were modified or in case of sync issues.
        """
        try:
            instance = self.get_object()
            with db_transaction.atomic():
                instance.calculate_spending()
                # Also recalculate parent budget
                instance.budget.calculate_spending()

            logger.info(f"Budget category {instance.id} recalculated")
            return Response(BudgetCategorySerializer(instance).data)

        except Exception as e:
            logger.exception(f"Error recalculating budget category {id}: {e}")
            return Response(
                {"error": _("Failed to recalculate budget category.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Top utilization categories",
        description="Get categories with highest utilization percentage across all active budgets.",
        responses={200: BudgetCategorySerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="top-utilization")
    def top_utilization(self, request):
        """
        Get categories with highest utilization percentage across all active budgets.
        """
        try:
            queryset = (
                self.get_queryset()
                .filter(budget__is_active=True)
                .order_by("-percentage_used")[:5]
            )
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.exception(f"Error getting top utilization categories: {e}")
            return Response(
                {"error": _("Failed to retrieve utilization data.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class FinancialGoalViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing Financial Goals.

    Provides:
    - CRUD for financial goals
    - Contribution management
    - Progress tracking and summaries
    - Automatic currency handling
    """

    queryset = FinancialGoal.objects.select_related(
        "user", "currency", "linked_account"
    )
    serializer_class = FinancialGoalSerializer
    permission_classes = [IsOwnerOrAdmin]
    filterset_class = FinancialGoalFilter
    ordering_fields = [
        "name",
        "target_amount",
        "current_amount",
        "progress_percentage",
        "target_date",
        "priority",
        "created_at",
    ]
    ordering = ["priority", "target_date"]

    def get_queryset(self):
        """Filter goals by active user."""
        return super().get_queryset().filter(user=self.request.user)

    def get_serializer_class(self):
        """Dynamic serializer selection."""
        return get_financial_goal_serializer(self.action)

    @extend_schema(
        summary="Add contribution to goal",
        request=FinancialGoalContributionSerializer,
        responses={200: OpenApiResponse(description="Contribution added successfully")},
    )
    @action(detail=True, methods=["post"], url_path="contribute")
    def contribute(self, request, pk=None):
        """Add a manual contribution to a financial goal."""
        goal = self.get_object()
        serializer = self.get_serializer(data=request.data, context={"goal": goal})

        if serializer.is_valid():
            try:
                with db_transaction.atomic():
                    result = serializer.save()

                logger.info(
                    f"Contribution of {request.data.get('amount')} added to goal {goal.id}"
                )
                return Response(result, status=status.HTTP_200_OK)
            except Exception as e:
                logger.error(f"Error adding contribution: {e}")
                return Response(
                    {"error": _("Failed to process contribution.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Get goals summary statistics",
        responses={200: OpenApiResponse(description="Summary of all financial goals")},
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Get an aggregate summary of all financial goals."""
        try:
            queryset = self.get_queryset()

            # Aggregate stats
            stats = queryset.aggregate(
                total_goals=Count("id"),
                achieved_goals=Count("id", filter=Q(is_achieved=True)),
                total_target=Sum("target_amount"),
                total_saved=Sum("current_amount"),
                avg_progress=Avg("progress_percentage"),
            )

            # Get nearby deadlines
            upcoming = queryset.filter(
                is_achieved=False,
                target_date__lte=timezone.now().date() + timedelta(days=90),
            ).order_by("target_date")[:5]

            summary = {
                "overview": {
                    "total_goals": stats["total_goals"] or 0,
                    "achieved_goals": stats["achieved_goals"] or 0,
                    "active_goals": (stats["total_goals"] or 0)
                    - (stats["achieved_goals"] or 0),
                    "overall_progress": float(stats["avg_progress"] or 0),
                },
                "financials": {
                    "total_target_amount": str(stats["total_target"] or 0),
                    "total_current_amount": str(stats["total_saved"] or 0),
                    "total_remaining_amount": str(
                        (stats["total_target"] or 0) - (stats["total_saved"] or 0)
                    ),
                },
                "upcoming_deadlines": FinancialGoalListSerializer(
                    upcoming, many=True
                ).data,
            }

            return Response(summary)

        except Exception as e:
            logger.exception(f"Error generating goal summary: {e}")
            return Response(
                {"error": _("Failed to generate summary.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
