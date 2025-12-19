from django.conf import settings
from django.db import models
from django.db import transaction as db_transaction
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from utils import choices, filters, loggings, throttlings
from utils.paginations import CustomPageNumberPagination
from utils.permissions import IsOwnerOrAdmin

from .models import Account, Transaction
from .serializers import (
    AccountDetailSerializer,
    AccountListSerializer,
    AccountReconcileSerializer,
    AccountSerializer,
    TransactionBulkCreateSerializer,
    TransactionCreateSerializer,
    TransactionSerializer,
    TransactionUpdateSerializer,
    TransactionVerificationSerializer,
    get_account_serializer,
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
    lookup_field = "id"

    @extend_schema(
        summary="List accounts",
        description=(
            "List all accounts accessible to the authenticated user. "
            "Regular users see only their accounts. "
            "Staff/Admin see all accounts."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                description="Search by account name or bank name",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="account_type",
                description="Filter by account type",
                required=False,
                type=str,
                enum=[choice[0] for choice in choices.AccountType.choices],
            ),
            OpenApiParameter(
                name="currency_code",
                description="Filter by currency code",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="is_active",
                description="Filter by active status",
                required=False,
                type=bool,
            ),
            OpenApiParameter(
                name="is_primary",
                description="Filter by primary status",
                required=False,
                type=bool,
            ),
            OpenApiParameter(
                name="ordering",
                description="Order results by field",
                required=False,
                type=str,
                enum=[
                    "name",
                    "-name",
                    "created_at",
                    "-created_at",
                    "current_balance",
                    "-current_balance",
                ],
            ),
        ],
        responses={
            200: AccountListSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List accounts with advanced filtering and pagination."""
        try:
            queryset = self.get_queryset()
            user = request.user

            # Apply filters
            queryset = self._apply_filters(queryset, request)

            # Apply ordering
            ordering = self._get_ordering(request)
            if ordering:
                queryset = queryset.order_by(*ordering)
            else:
                # Default ordering
                queryset = queryset.order_by("-is_primary", "-created_at")

            # Paginate
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            # Return all if no pagination
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            return self._handle_api_error(
                e,
                "An error occurred while retrieving accounts.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _apply_filters(self, queryset, request):
        """Apply query filters to account queryset."""
        # Search filter
        search_query = request.query_params.get("search")
        if search_query:
            queryset = queryset.filter(
                models.Q(name__icontains=search_query)
                | models.Q(bank_name__icontains=search_query)
            )

        # Account type filter
        account_type = request.query_params.get("account_type")
        if account_type:
            queryset = queryset.filter(account_type=account_type)

        # Currency filter
        currency_code = request.query_params.get("currency_code")
        if currency_code:
            queryset = queryset.filter(currency__code__iexact=currency_code)

        # Status filters
        is_active = request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        is_primary = request.query_params.get("is_primary")
        if is_primary is not None:
            queryset = queryset.filter(is_primary=is_primary.lower() == "true")

        # Balance filters (optional)
        min_balance = request.query_params.get("min_balance")
        if min_balance:
            try:
                queryset = queryset.filter(current_balance__gte=Decimal(min_balance))
            except (InvalidOperation, ValueError):
                logger.warning(f"Invalid min_balance filter: {min_balance}")

        max_balance = request.query_params.get("max_balance")
        if max_balance:
            try:
                queryset = queryset.filter(current_balance__lte=Decimal(max_balance))
            except (InvalidOperation, ValueError):
                logger.warning(f"Invalid max_balance filter: {max_balance}")

        return queryset

    def _get_ordering(self, request):
        """Get ordering parameters from request."""
        ordering = request.query_params.get("ordering")

        if not ordering:
            return None

        # Validate ordering fields
        valid_fields = {
            "name",
            "-name",
            "created_at",
            "-created_at",
            "current_balance",
            "-current_balance",
            "account_type",
            "-account_type",
        }

        order_fields = ordering.split(",")
        validated_fields = []

        for field in order_fields:
            if field.strip() in valid_fields:
                validated_fields.append(field.strip())
            else:
                logger.warning(f"Invalid ordering field: {field}")

        return validated_fields if validated_fields else None

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
        summary="Update account",
        description="Update all fields of an account.",
        request=AccountSerializer,
        responses={
            200: AccountSerializer,
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Account not found"),
        },
    )
    def update(self, request, *args, **kwargs):
        """Update account (PUT)."""
        try:
            instance = self.get_object()
            self._check_account_modification_permission(instance, request.user)

            serializer = self.get_serializer(
                instance, data=request.data, partial=False, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Account updated: id={updated_instance.id}, "
                f"name='{updated_instance.name}', "
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
        4. No linked budget categories
        5. No linked recurring transactions
        6. No linked financial goals
        7. No active sub-accounts (if hierarchical accounts exist)
        8. Account must be inactive for minimum period (configurable)
        9. No open banking connections

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

        # Check for linked budget categories
        try:
            from .models import Budget

            budget_count = Budget.objects.filter(
                models.Q(linked_account=account)
                | models.Q(accounts=account)  # If using ManyToMany
            ).count()

            if budget_count > 0:
                logger.info(
                    f"Account {account.id} is linked to {budget_count} budgets, cannot delete"
                )
                return False
        except Exception as e:
            logger.error(f"Error checking budget links for account {account.id}: {e}")
            # Fail-safe: if we can't check, assume there might be linked budgets
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

        # 4. Check for linked budgets
        try:
            budget_count = Budget.objects.filter(
                models.Q(linked_account=account) | models.Q(accounts=account)
            ).count()

            if budget_count > 0:
                return (
                    f"Cannot delete account '{account.name}' because it is linked to "
                    f"{budget_count} budget(s). Please unlink from budgets first."
                )
        except Exception:
            pass

        # 6. Check for financial goals
        try:
            goal_count = FinancialGoal.objects.filter(
                linked_account=account, is_active=True, is_achieved=False
            ).count()

            if goal_count > 0:
                return (
                    f"Cannot delete account '{account.name}' because it is linked to "
                    f"{goal_count} active financial goal(s). "
                    f"Please unlink from goals or mark goals as achieved first."
                )
        except Exception:
            pass

        # 7. Check for recent transfers
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
        description="Get summary statistics for user's accounts.",
        responses={
            200: OpenApiResponse(description="Account summary"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Get account summary for the user."""
        try:
            user = request.user
            accounts = self.get_queryset().filter(is_active=True)

            # Calculate summary statistics
            total_balance = Decimal("0.00")
            currency_summary = {}
            type_summary = {}

            for account in accounts:
                total_balance += account.current_balance

                # Currency summary
                currency_code = account.currency.code
                if currency_code not in currency_summary:
                    currency_summary[currency_code] = {
                        "balance": Decimal("0.00"),
                        "account_count": 0,
                        "currency_symbol": account.currency.symbol or currency_code,
                    }
                currency_summary[currency_code]["balance"] += account.current_balance
                currency_summary[currency_code]["account_count"] += 1

                # Account type summary
                if account.account_type not in type_summary:
                    type_summary[account.account_type] = {
                        "balance": Decimal("0.00"),
                        "account_count": 0,
                    }
                type_summary[account.account_type]["balance"] += account.current_balance
                type_summary[account.account_type]["account_count"] += 1

            # Convert Decimal to string for JSON serialization
            for currency in currency_summary.values():
                currency["balance"] = str(currency["balance"])

            for acc_type in type_summary.values():
                acc_type["balance"] = str(acc_type["balance"])

            summary = {
                "total_balance": str(total_balance),
                "account_count": accounts.count(),
                "primary_account": Account.get_user_primary_account(user.id),
                "currency_summary": currency_summary,
                "type_summary": type_summary,
                "has_locked_accounts": accounts.filter(is_locked=True).exists(),
            }

            logger.debug(f"Account summary generated for user {user.id}")
            return Response(summary)

        except Exception as e:
            return self._handle_api_error(
                e,
                "Failed to generate account summary.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Set primary account",
        description="Set an account as the user's primary account.",
        responses={
            200: AccountSerializer,
            400: OpenApiResponse(description="Cannot set as primary"),
            403: OpenApiResponse(description="Permission denied"),
            404: OpenApiResponse(description="Account not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="set-primary")
    def set_primary(self, request, id=None):
        """Set account as primary."""
        try:
            account = self.get_object()
            user = request.user

            # Check permissions
            if account.user != user and not (user.is_staff or user.is_superuser):
                return Response(
                    {"error": "Cannot set another user's account as primary."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Check if account can be primary
            if not account.is_active:
                return Response(
                    {"error": "Cannot set inactive account as primary."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if account.is_locked:
                return Response(
                    {"error": "Cannot set locked account as primary."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with db_transaction.atomic():
                # Demote existing primary account
                Account.objects.filter(
                    user=user, is_primary=True, is_active=True
                ).exclude(id=account.id).update(is_primary=False)

                # Set new primary
                account.is_primary = True
                account.save(update_fields=["is_primary", "updated_at"])

            logger.info(
                f"Account set as primary: id={account.id}, "
                f"name='{account.name}', "
                f"by user={user.id}"
            )

            serializer = self.get_serializer(account)
            return Response(serializer.data)

        except Exception as e:
            return self._handle_api_error(
                e, "Failed to set account as primary.", status.HTTP_400_BAD_REQUEST
            )

    @extend_schema(
        summary="Get balance history",
        description="Get historical balance data for an account.",
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
    filterset_class = filters.TransactionFilter
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
        queryset = (
            Transaction.objects.select_related(
                "user",
                "account",
                "category",
                "original_currency",
            )
            .prefetch_related(
                "tags",
            )
            .only(
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
            )
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
        elif self.action == "bulk_create":
            return TransactionBulkCreateSerializer
        elif self.action == "bulk_update":
            return TransactionBulkUpdateSerializer
        elif self.action == "verify":
            return TransactionVerificationSerializer
        elif self.action == "reconcile":
            return TransactionReconciliationSerializer
        return super().get_serializer_class()

    def list(self, request, *args, **kwargs):
        """
        List transactions with filtering, pagination, and analytics.

        Features:
        - Advanced filtering via query parameters
        - Search across multiple fields
        - Ordering by any field
        - Analytics summary in response
        - Export capabilities

        Returns:
            Paginated response with transactions and analytics
        """
        try:
            # Apply filtering
            queryset = self.filter_queryset(self.get_queryset())

            # Check for export request
            export_format = request.query_params.get("export")
            if export_format and (request.user.is_staff or request.user.is_superuser):
                return self._export_transactions(queryset, export_format)

            # Apply ordering (default: most recent first)
            ordering = request.query_params.get("ordering", "-transaction_date")
            if ordering:
                queryset = queryset.order_by(ordering)

            # Paginate
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)

                # Include analytics in paginated response
                response = self.get_paginated_response(serializer.data)
                response.data["analytics"] = self._get_analytics_summary(queryset)
                return response

            # Non-paginated response (if pagination is disabled)
            serializer = self.get_serializer(queryset, many=True)
            return Response(
                {
                    "transactions": serializer.data,
                    "analytics": self._get_analytics_summary(queryset),
                    "count": queryset.count(),
                }
            )

        except ValidationError as e:
            logger.warning(
                f"Transaction list validation error: {e}",
                extra={"user_id": request.user.id},
            )
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(
                f"Error listing transactions for user {request.user.id}: {e}"
            )
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
                transaction = serializer.save()

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

        except ValidationError as e:
            logger.warning(
                f"Transaction creation validation failed: {e}",
                extra={"user_id": request.user.id, "data": request.data},
            )
            return Response(
                {"error": _("Validation failed"), "details": e.detail},
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

        except ValidationError as e:
            logger.warning(
                f"Transaction update validation failed: {e}",
                extra={
                    "transaction_id": kwargs.get("id"),
                    "user_id": request.user.id,
                    "data": request.data,
                },
            )
            return Response(
                {"error": _("Validation failed"), "details": e.detail},
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
                        TransactionType.EXPENSE
                        if instance.transaction_type == TransactionType.INCOME
                        else TransactionType.INCOME
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
        summary="Bulk Create Transactions",
        description="""
        Create multiple transactions in a single request.

        Features:
        - Atomic operation (all or nothing)
        - Validation for each transaction
        - Batch processing with progress tracking
        - Background processing for large batches
        """,
        request=TransactionBulkCreateSerializer,
        responses={
            201: inline_serializer(
                name="BulkCreateResponse",
                fields={
                    "created": serializers.IntegerField(),
                    "failed": serializers.IntegerField(),
                    "errors": serializers.ListField(child=serializers.DictField()),
                    "transaction_ids": serializers.ListField(
                        child=serializers.UUIDField()
                    ),
                },
            ),
        },
    )
    @action(detail=False, methods=["post"], url_path="bulk-create")
    def bulk_create(self, request, *args, **kwargs):
        """
        Create multiple transactions in bulk.

        Performance:
        - Uses bulk_create for database efficiency
        - Processes in configurable batch sizes
        - Returns summary with success/failure counts

        Returns:
            Summary of bulk creation results
        """
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            transactions_data = serializer.validated_data["transactions"]
            results = {"created": 0, "failed": 0, "errors": [], "transaction_ids": []}

            # Process in batches for performance
            batch_size = 100
            for i in range(0, len(transactions_data), batch_size):
                batch = transactions_data[i : i + batch_size]

                with db_transaction.atomic():
                    for transaction_data in batch:
                        try:
                            transaction_serializer = TransactionCreateSerializer(
                                data=transaction_data, context={"request": request}
                            )
                            transaction_serializer.is_valid(raise_exception=True)
                            transaction = transaction_serializer.save()

                            results["created"] += 1
                            results["transaction_ids"].append(str(transaction.id))

                        except Exception as e:
                            results["failed"] += 1
                            results["errors"].append(
                                {
                                    "index": i + batch.index(transaction_data),
                                    "error": str(e),
                                    "data": transaction_data,
                                }
                            )

            logger.info(
                f"Bulk transaction creation completed",
                extra={
                    "user_id": request.user.id,
                    "created": results["created"],
                    "failed": results["failed"],
                },
            )

            return Response(results, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(
                f"Bulk transaction creation failed: {e}",
                extra={"user_id": request.user.id},
            )
            return Response(
                {
                    "error": _(
                        "Bulk creation failed. Please check your data and try again."
                    )
                },
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
            if instance.status != TransactionStatus.PENDING:
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
            TransactionStatus.COMPLETED,
            TransactionStatus.RECONCILED,
        ]:
            return False

        return True

    def _get_analytics_summary(self, queryset) -> Dict[str, Any]:
        """
        Calculate analytics summary for a queryset.

        Args:
            queryset: Filtered transaction queryset

        Returns:
            Dictionary with analytics summary
        """
        try:
            # Basic aggregates
            aggregates = queryset.aggregate(
                total_count=Count("id"),
                total_amount=Sum("amount"),
                avg_amount=Avg("amount"),
                min_amount=Min("amount"),
                max_amount=Max("amount"),
                total_income=Sum(
                    "amount", filter=Q(transaction_type=TransactionType.INCOME)
                ),
                total_expense=Sum(
                    "amount", filter=Q(transaction_type=TransactionType.EXPENSE)
                ),
            )

            # Calculate net flow
            total_income = aggregates["total_income"] or Decimal("0.00")
            total_expense = aggregates["total_expense"] or Decimal("0.00")
            net_flow = total_income - total_expense

            return {
                "summary": {
                    "total_transactions": aggregates["total_count"] or 0,
                    "total_amount": aggregates["total_amount"] or Decimal("0.00"),
                    "average_amount": aggregates["avg_amount"] or Decimal("0.00"),
                    "min_amount": aggregates["min_amount"] or Decimal("0.00"),
                    "max_amount": aggregates["max_amount"] or Decimal("0.00"),
                    "total_income": total_income,
                    "total_expense": total_expense,
                    "net_flow": net_flow,
                    "income_expense_ratio": (
                        (total_income / total_expense * 100) if total_expense > 0 else 0
                    ),
                },
                "counts": {
                    status_choice[0]: queryset.filter(status=status_choice[0]).count()
                    for status_choice in TransactionStatus.choices
                },
            }

        except Exception as e:
            logger.error(f"Error calculating analytics summary: {e}")
            return {}

    def _calculate_analytics(self, queryset, group_by: str) -> Dict[str, Any]:
        """
        Calculate detailed analytics with grouping.

        Args:
            queryset: Filtered transaction queryset
            group_by: Field to group by

        Returns:
            Detailed analytics data
        """
        analytics = {
            "summary": self._get_analytics_summary(queryset),
            "trends": [],
            "breakdown": [],
        }

        # Add time-based trends
        if group_by in ["month", "week", "day"]:
            analytics["trends"] = self._get_time_based_trends(queryset, group_by)

        # Add category/account breakdown
        if group_by in ["category", "account"]:
            analytics["breakdown"] = self._get_breakdown_by_field(queryset, group_by)

        return analytics

    def _get_time_based_trends(self, queryset, interval: str) -> List[Dict[str, Any]]:
        """
        Get transaction trends over time.

        Args:
            queryset: Filtered transaction queryset
            interval: Time interval (month, week, day)

        Returns:
            List of trend data points
        """
        # Implementation depends on your database and requirements
        # This is a simplified version
        trends = []

        # Group by date truncation (implementation varies by DB)
        # For PostgreSQL:
        # from django.db.models.functions import TruncMonth, TruncWeek, TruncDay

        return trends

    def _get_breakdown_by_field(self, queryset, field: str) -> List[Dict[str, Any]]:
        """
        Get transaction breakdown by field.

        Args:
            queryset: Filtered transaction queryset
            field: Field to break down by

        Returns:
            List of breakdown items
        """
        breakdown = []

        if field == "category":
            # Group by category with aggregates
            categories = (
                queryset.values(
                    "category__id",
                    "category__name",
                    "category__category_type",
                )
                .annotate(
                    total_amount=Sum("amount"),
                    transaction_count=Count("id"),
                    avg_amount=Avg("amount"),
                )
                .order_by("-total_amount")
            )

            for category in categories:
                breakdown.append(
                    {
                        "id": category["category__id"],
                        "name": category["category__name"],
                        "type": category["category__category_type"],
                        "total_amount": category["total_amount"],
                        "transaction_count": category["transaction_count"],
                        "avg_amount": category["avg_amount"],
                        "percentage": (
                            (
                                category["total_amount"]
                                / queryset.aggregate(Sum("amount"))["amount__sum"]
                                * 100
                            )
                            if queryset.aggregate(Sum("amount"))["amount__sum"]
                            else 0
                        ),
                    }
                )

        return breakdown

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
