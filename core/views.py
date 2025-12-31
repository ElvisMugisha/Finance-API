from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from utils import choices, loggings
from utils.filters import CategoryFilter, CurrencyFilter
from utils.paginations import CustomPageNumberPagination
from utils.permissions import (
    CategoryPermission,
    IsActiveAndVerified,
    IsAdminOnly,
    IsStaffOrAdmin,
)

from .models import Category, Currency
from .serializers import (
    CategoryCreateUpdateSerializer,
    CategoryDetailSerializer,
    CategoryListSerializer,
    CategoryTreeSerializer,
    CurrencyConversionSerializer,
    CurrencyDetailSerializer,
    CurrencyListSerializer,
    CurrencySerializer,
    ExchangeRateUpdateSerializer,
)

# Initialize logger
logger = loggings.setup_logging()


class BaseCurrencyViewSet(viewsets.GenericViewSet):
    """Base ViewSet with common currency functionality."""

    permission_classes = [IsActiveAndVerified]
    pagination_class = CustomPageNumberPagination

    def get_queryset(self):
        """
        Get currencies based on user role and query parameters.
        Returns a filtered and ordered queryset.
        """
        user = self.request.user
        queryset = Currency.objects.all()

        if not (user.is_staff or user.is_superuser):
            queryset = queryset.filter(is_active=True)

        # Apply advanced filtering using CurrencyFilter
        return CurrencyFilter(self.request.query_params, queryset=queryset).qs

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "list":
            return CurrencyListSerializer
        elif self.action == "retrieve":
            return CurrencyDetailSerializer
        return CurrencySerializer


class CurrencyViewSet(
    BaseCurrencyViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    ViewSet for Currency CRUD operations with role-based permissions.

    Permissions:
    - List/Retrieve: All active & verified users
    - Create: Staff & Admin users
    - Update/Delete: Admin only
    - Special actions (update_base, update_rates): Admin only
    """

    queryset = Currency.objects.all()
    lookup_field = "pk"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_permissions(self):
        """Set permissions per action."""
        if self.action in ["create"]:
            return [IsStaffOrAdmin()]
        elif self.action in ["update", "partial_update", "destroy"]:
            return [IsAdminOnly()]
        elif self.action in ["update_base_currency", "update_exchange_rates"]:
            return [IsAdminOnly()]
        return [IsActiveAndVerified()]

    @extend_schema(
        summary="List currencies",
        description="Retrieve paginated list of currencies visible to the user.",
        responses={200: CurrencyListSerializer(many=True)},
        parameters=[
            OpenApiParameter(
                name="search",
                description="Search by currency code or name",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="is_active",
                description="Filter by active status (staff/admin only)",
                required=False,
                type=bool,
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """
        List currencies with advanced filtering, search, and ordering.
        """
        try:
            queryset = self.get_queryset()
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(
                f"CurrencyViewSet.list failed for user {request.user}: {e}"
            )
            return Response(
                {"detail": _("Failed to retrieve currencies.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create currencies (Bulk supported)",
        description="Create one or multiple currencies. Staff & Admin only.",
        request=CurrencySerializer(many=True),
        responses={
            201: CurrencySerializer(many=True),
            207: CurrencySerializer(many=True),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create one or multiple currencies."""
        try:
            is_bulk = isinstance(request.data, list)
            serializer = self.get_serializer(data=request.data, many=is_bulk)
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                serializer.save()

            logger.info(f"Currencies created by {request.user}")

            status_code = (
                status.HTTP_201_CREATED if not is_bulk else status.HTTP_207_MULTI_STATUS
            )
            return Response(serializer.data, status=status_code)

        except Exception as e:
            logger.exception(f"Error creating currencies: {e}")
            return Response(
                {"detail": _("Failed to create currencies. Please check the data.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Update currency",
        description="Update specific currency fields. Admin only.",
        request=CurrencySerializer,
        responses={200: CurrencySerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        """Partial update currency - admin only."""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                serializer.save()

            logger.info(
                f"Currency '{instance.code}' partially updated by admin {request.user.email}"
            )
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error partially updating currency: {e}")
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Delete currency",
        description="Soft delete a currency (set is_active=False). Admin only.",
        responses={204: OpenApiResponse(description="No Content")},
    )
    def destroy(self, request, *args, **kwargs):
        """Soft delete currency by setting is_active=False."""
        try:
            instance = self.get_object()

            # Check if currency is in use
            if self._is_currency_in_use(instance):
                return Response(
                    {
                        "detail": _(
                            "Cannot delete currency because it is used by accounts or transactions."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])

            logger.info(
                f"Currency '{instance.code}' deactivated by admin {request.user.email}"
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            logger.exception(f"Error deleting currency: {e}")
            return Response(
                {"error": "Failed to delete currency."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _is_currency_in_use(self, currency):
        """Check if currency is used by any account or transaction."""
        from accounts.models import Account, Transaction

        account_count = Account.objects.filter(currency=currency).count()
        transaction_count = Transaction.objects.filter(
            models.Q(original_currency=currency) | models.Q(account__currency=currency)
        ).count()

        return account_count > 0 or transaction_count > 0

    @extend_schema(
        summary="Set base currency",
        description="Set a currency as the system's base currency. Admin only.",
        request=None,
        responses={
            200: CurrencySerializer,
            400: OpenApiResponse(description="Bad Request"),
        },
    )
    @action(detail=True, methods=["post"], url_path="set-base")
    def update_base_currency(self, request, pk=None):
        """
        Set this currency as the system's base currency.
        The demoting of previous base currency is handled at the model level.
        """
        currency = self.get_object()

        if not currency.is_active:
            raise ValidationError(
                {"detail": _("Cannot set inactive currency as base.")}
            )

        try:
            with db_transaction.atomic():
                currency.is_base_currency = True
                currency.save()

            logger.info(
                f"Currency '{currency.code}' promoted to base by {request.user.email}"
            )
            serializer = self.get_serializer(currency)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error setting base currency {currency.code}: {e}")
            return Response(
                {"detail": _("Failed to update base currency.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Update exchange rates",
        description="Batch update exchange rates from external source. Admin only.",
        request=ExchangeRateUpdateSerializer,
        responses={200: ExchangeRateUpdateSerializer},
    )
    @action(detail=False, methods=["post"], url_path="update-rates")
    def update_exchange_rates(self, request):
        """Batch update exchange rates from external source."""
        try:
            serializer = ExchangeRateUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            stats = serializer.save()

            logger.info(
                f"Exchange rates updated by admin {request.user.email}: "
                f"{stats.get('updated', 0)} successful, {stats.get('failed', 0)} failed"
            )

            return Response(stats)

        except Exception as e:
            logger.exception(f"Error updating exchange rates: {e}")
            return Response(
                {"error": "Failed to update exchange rates."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Convert currency",
        description="Convert amount between currencies.",
        request=CurrencyConversionSerializer,
        responses={200: CurrencyConversionSerializer},
    )
    @action(detail=False, methods=["post"], url_path="convert")
    def convert_currency(self, request):
        """Convert amount from one currency to another."""
        try:
            serializer = CurrencyConversionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            result = serializer.save()

            logger.debug(
                f"Currency conversion: {request.data.get('amount')} "
                f"{request.data.get('source_currency')} -> "
                f"{result.get('converted_amount')} {request.data.get('target_currency')}"
            )

            return Response(result)

        except Exception as e:
            logger.exception(f"Error converting currency: {e}")
            return Response(
                {"detail": _("Currency conversion failed. Please check your inputs.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Get currency statistics",
        description="Get usage statistics for a currency. Admin only.",
        responses={200: OpenApiResponse(description="Statistics data")},
    )
    @action(detail=True, methods=["get"], url_path="stats")
    def currency_statistics(self, request, pk=None):
        """Get usage statistics for a currency."""
        try:
            currency = self.get_object()

            stats = {
                "currency": currency.code,
                "is_base_currency": currency.is_base_currency,
                "exchange_rate": str(currency.exchange_rate),
                "last_updated": currency.exchange_updated_at,
                "usage": self._get_currency_usage(currency),
                "historical_rates_count": len(currency.historical_rates),
            }

            return Response(stats)

        except Exception as e:
            logger.exception(f"Error getting currency statistics: {e}")
            return Response(
                {"detail": _("Failed to retrieve currency statistics.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_currency_usage(self, currency):
        """Get detailed usage statistics for currency."""
        from accounts.models import Account, Transaction

        try:
            # Get account usage
            accounts = Account.objects.filter(currency=currency)
            account_stats = {
                "total": accounts.count(),
                "active": accounts.filter(is_active=True).count(),
                "primary": accounts.filter(is_primary=True).count(),
            }

            # Get transaction usage
            transactions = Transaction.objects.filter(
                models.Q(original_currency=currency)
                | models.Q(account__currency=currency)
            )
            transaction_stats = {
                "total": transactions.count(),
                "completed": transactions.filter(status="completed").count(),
                "pending": transactions.filter(status="pending").count(),
            }

            return {
                "accounts": account_stats,
                "transactions": transaction_stats,
            }

        except Exception as e:
            logger.warning(f"Error getting usage stats for {currency.code}: {e}")
            return {}


class CategoryViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Comprehensive ViewSet for category management.

    Features:
    1. Role-based category ownership (user vs system)
    2. Unified create method (handles single and bulk)
    3. PATCH-only updates (no PUT, no bulk update)
    4. Comprehensive filtering and search
    5. Proper error handling and logging
    """

    queryset = Category.objects.all()
    permission_classes = [CategoryPermission]
    pagination_class = CustomPageNumberPagination
    lookup_field = "pk"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    # Serializer mapping
    serializer_action_map = {
        "list": CategoryListSerializer,
        "retrieve": CategoryDetailSerializer,
        "create": CategoryCreateUpdateSerializer,
        "partial_update": CategoryCreateUpdateSerializer,
        "tree": CategoryTreeSerializer,
        "system": CategoryListSerializer,
        "mine": CategoryListSerializer,
    }

    def get_queryset(self):
        """
        Get categories based on user permissions.
        Staff/Admin: all categories
        Regular users: their categories + system categories
        """
        return Category.objects.visible_to(self.request.user)

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        return self.serializer_action_map.get(
            self.action, CategoryCreateUpdateSerializer
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Search categories by name",
                required=False,
            ),
            OpenApiParameter(
                name="category_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by category type",
                enum=[choice[0] for choice in choices.TransactionType.choices],
                required=False,
            ),
            OpenApiParameter(
                name="parent_id",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by parent category ID",
                required=False,
            ),
            OpenApiParameter(
                name="include_inactive",
                type=bool,
                location=OpenApiParameter.QUERY,
                description="Include inactive categories",
                required=False,
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        """
        List categories with comprehensive filtering.
        """
        return self._get_filtered_response(request)

    def _get_filtered_response(
        self, request, is_system=False, is_mine=False, extra_filters=None
    ):
        """Helper to apply filters, paginate and return response."""
        try:
            queryset = self.get_queryset()

            if extra_filters:
                queryset = queryset.filter(**extra_filters)

            # Apply filters via CategoryFilter
            filter_instance = CategoryFilter(
                request, queryset, is_system=is_system, is_mine=is_mine
            )
            filtered_queryset = filter_instance.apply()

            page = self.paginate_queryset(filtered_queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(filtered_queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error in CategoryViewSet filtering: {e}")
            return Response(
                {"detail": _("Failed to retrieve categories.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def create(self, request, *args, **kwargs):
        """
        Unified creation endpoint for single or multiple categories.
        Logic for ownership (system vs personal) is handled in the serializer.
        """
        data = request.data
        is_bulk = isinstance(data, list)

        if is_bulk:
            # Enforce batch size limit for stability
            max_batch = getattr(settings, "MAX_BATCH_SIZE", 100)
            if len(data) > max_batch:
                raise ValidationError(
                    {"detail": _(f"Cannot create more than {max_batch} items at once.")}
                )

        serializer = self.get_serializer(data=data, many=is_bulk)
        serializer.is_valid(raise_exception=True)

        try:
            with db_transaction.atomic():
                self.perform_create(serializer)

            headers = self.get_success_headers(serializer.data)
            return Response(
                serializer.data, status=status.HTTP_201_CREATED, headers=headers
            )

        except Exception as e:
            logger.exception(f"Category creation failed: {e}")
            return Response(
                {"detail": _("Failed to create category/categories.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def partial_update(self, request, *args, **kwargs):
        """
        Update specific fields of a category.
        Security logic is centralized in the serializer.
        """
        try:
            instance = self.get_object()
            self.check_object_permissions(request, instance)

            serializer = self.get_serializer(
                instance, data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Category updated: id={updated_instance.id} by {request.user.email}"
            )
            return Response(serializer.data)

        except (PermissionDenied, ValidationError) as e:
            raise e
        except Exception as e:
            logger.exception(f"Category update failed: {e}")
            return Response(
                {"detail": _("Failed to update category.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def destroy(self, request, *args, **kwargs):
        """
        Soft delete a category.
        Consolidates all deletion rules (transactions, children, permissions).
        """
        try:
            instance = self.get_object()
            self.check_object_permissions(request, instance)

            # Perform unified validation
            self._validate_deletion(instance, request.user)

            with db_transaction.atomic():
                instance.is_active = False
                instance.save(update_fields=["is_active", "updated_at"])

            logger.info(
                f"Category '{instance.name}' soft-deleted by {request.user.email}"
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        except (PermissionDenied, ValidationError) as e:
            raise e
        except Exception as e:
            logger.exception(f"Category deletion failed: {e}")
            return Response(
                {"detail": _("Failed to delete category.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _validate_deletion(self, category, user):
        """Unified validation for category deletion."""
        # 1. Active transactions check
        if category.transaction_count > 0:
            raise ValidationError(
                _("Cannot delete category with existing transactions.")
            )

        # 2. Active subcategories check
        if category.children.filter(is_active=True).exists():
            raise ValidationError(
                _("Cannot delete category with active subcategories.")
            )

        # 3. Ownership / System status protection
        is_staff = user.is_staff or user.is_superuser
        if category.is_system_category and not is_staff:
            raise PermissionDenied(_("System categories can only be deleted by staff."))

        if not category.is_system_category and category.user != user and not is_staff:
            raise PermissionDenied(
                _("You do not have permission to delete this category.")
            )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Search categories by name",
                required=False,
            ),
            OpenApiParameter(
                name="category_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by category type",
                enum=[choice[0] for choice in choices.TransactionType.choices],
                required=False,
            ),
            OpenApiParameter(
                name="parent_id",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter by parent category ID",
                required=False,
            ),
        ]
    )
    @action(detail=False, methods=["get"])
    def tree(self, request):
        """Get hierarchical category tree."""
        extra_filters = {}
        if not request.query_params.get("parent_id"):
            extra_filters["parent__isnull"] = True

        # Optimize tree retrieval with children prefetching
        self.queryset = self.get_queryset().prefetch_related("children")

        return self._get_filtered_response(request, extra_filters=extra_filters)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Search system categories by name",
                required=False,
            ),
            OpenApiParameter(
                name="category_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter system categories by type",
                enum=[choice[0] for choice in choices.TransactionType.choices],
                required=False,
            ),
            OpenApiParameter(
                name="parent_id",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter system categories by parent ID",
                required=False,
            ),
        ]
    )
    @action(detail=False, methods=["get"])
    def system(self, request):
        """Get all system categories."""
        return self._get_filtered_response(request, is_system=True)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Search your categories by name",
                required=False,
            ),
            OpenApiParameter(
                name="category_type",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter your categories by type",
                enum=[choice[0] for choice in choices.TransactionType.choices],
                required=False,
            ),
            OpenApiParameter(
                name="parent_id",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Filter your categories by parent ID",
                required=False,
            ),
        ]
    )
    @action(detail=False, methods=["get"])
    def mine(self, request):
        """Get categories belonging to the current user."""
        return self._get_filtered_response(request, is_mine=True)
