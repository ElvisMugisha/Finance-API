from decimal import Decimal

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
    NotAuthenticated,
    NotFound,
)

from django.db import models, transaction as db_transaction
from django.utils import timezone
from django.conf import settings

from utils import loggings, choices
from utils.paginations import CustomPageNumberPagination
from utils.filters import CategoryFilter

from .models import Category, Currency
from utils.permissions import (
    IsActiveAndVerified,
    IsStaffOrAdmin,
    IsAdminOnly,
    CategoryPermission,
)
from .serializers import (
    CurrencySerializer,
    CurrencyListSerializer,
    CurrencyDetailSerializer,
    CurrencyConversionSerializer,
    ExchangeRateUpdateSerializer,
    CategoryListSerializer,
    CategoryDetailSerializer,
    CategoryCreateUpdateSerializer,
    CategoryTreeSerializer,
)

# Initialize logger
logger = loggings.setup_logging()


class BaseCurrencyViewSet(viewsets.GenericViewSet):
    """Base ViewSet with common currency functionality."""

    permission_classes = [IsActiveAndVerified]
    pagination_class = CustomPageNumberPagination

    def get_queryset(self):
        """Filter currencies based on user permissions."""
        user = self.request.user
        queryset = Currency.objects.all()

        # Regular users only see active currencies
        if not (user.is_staff or user.is_superuser):
            queryset = queryset.filter(is_active=True)

        return queryset.order_by("name")

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
        """List currencies with optional filtering."""
        try:
            queryset = self.get_queryset()

            # Apply filters
            search_query = request.query_params.get("search")
            if search_query:
                queryset = queryset.filter(
                    models.Q(code__icontains=search_query)
                    | models.Q(name__icontains=search_query)
                )

            # Staff/Admin can filter by is_active
            if request.user.is_staff or request.user.is_superuser:
                is_active = request.query_params.get("is_active")
                if is_active:  # Check for non-empty string
                    if is_active.lower() == "true":
                        queryset = queryset.filter(is_active=True)
                    elif is_active.lower() == "false":
                        queryset = queryset.filter(is_active=False)

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error listing currencies: {e}")
            return Response(
                {"error": "An error occurred while retrieving currencies."},
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
                {"error": "Failed to create currencies. Please check the data."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Update currency",
        description="Update currency fields. Admin only.",
        request=CurrencySerializer,
        responses={200: CurrencySerializer},
    )
    def update(self, request, *args, **kwargs):
        """Update currency - admin only."""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=False)
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                serializer.save()

            logger.info(
                f"Currency '{instance.code}' updated by admin {request.user.email}"
            )
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error updating currency: {e}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

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

            # Check if trying to update protected fields
            protected_fields = ["exchange_rate", "exchange_source", "is_base_currency"]
            for field in protected_fields:
                if field in request.data and not request.user.is_superuser:
                    return Response(
                        {"error": f"Only super admins can update {field}"},
                        status=status.HTTP_403_FORBIDDEN,
                    )

            with db_transaction.atomic():
                serializer.save()

            logger.info(
                f"Currency '{instance.code}' partially updated by admin {request.user.email}"
            )
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error partially updating currency: {e}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

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
                        "error": f"Cannot delete currency '{instance.code}' "
                        f"because it's used by accounts or transactions."
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
        Demotes any existing base currency.
        """
        try:
            currency = self.get_object()

            if not currency.is_active:
                return Response(
                    {"error": "Cannot set inactive currency as base currency."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with db_transaction.atomic():
                # Demote existing base currency
                existing_base = Currency.get_base_currency()
                if existing_base and existing_base.id != currency.id:
                    existing_base.is_base_currency = False
                    existing_base.save(update_fields=["is_base_currency", "updated_at"])
                    logger.info(f"Demoted existing base currency: {existing_base.code}")

                # Promote new base currency
                currency.is_base_currency = True
                currency.exchange_rate = Decimal(
                    "1.0"
                )  # Base currency rate is always 1.0
                currency.save(
                    update_fields=["is_base_currency", "exchange_rate", "updated_at"]
                )

                logger.info(
                    f"Currency '{currency.code}' set as base by admin {request.user.email}"
                )

            serializer = self.get_serializer(currency)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error setting base currency: {e}")
            return Response(
                {"error": "Failed to set base currency."},
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
                {"error": "Currency conversion failed. Please check your inputs."},
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
                {"error": "Failed to retrieve currency statistics."},
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

        Rules:
        - Staff/Admin: all categories
        - Regular users: their categories + system categories
        """
        user = self.request.user

        if user.is_staff or user.is_superuser:
            return Category.objects.all()

        # Regular users see their categories + system categories
        return Category.objects.filter(
            models.Q(user=user) | models.Q(is_system_category=True)
        ).filter(is_active=True)

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

        Filters:
        - search: Search by name
        - category_type: Filter by type (income/expense)
        - parent_id: Filter by parent
        - include_inactive: Include inactive categories

        Permissions:
        - Staff/Admin: See all categories
        - Regular users: See their categories + system categories
        """
        try:
            queryset = self.get_queryset()

            # Apply filters
            filter_instance = CategoryFilter(request, queryset)
            filtered_queryset = filter_instance.apply_filters()

            # Paginate and serialize
            page = self.paginate_queryset(filtered_queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(filtered_queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error listing categories: {e}")
            return Response(
                {"error": "An error occurred while retrieving categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def create(self, request, *args, **kwargs):
        """
        Create category/categories.

        Features:
        - Handles both single object and list of objects
        - Automatic user assignment
        - System categories for staff/admin
        - Proper error handling for bulk operations
        """
        try:
            data = request.data
            user = request.user
            is_staff_or_admin = user.is_staff or user.is_superuser

            # Determine if this is a bulk or single create
            is_bulk = isinstance(data, list)

            if is_bulk:
                return self._bulk_create_categories(data, user, is_staff_or_admin)
            else:
                return self._create_single_category(data, user, is_staff_or_admin)

        except ValidationError as e:
            logger.warning(f"Validation error creating category: {e}")
            raise e
        except Exception as e:
            logger.exception(f"Error creating category: {e}")
            return Response(
                {"error": "Failed to create category."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _create_single_category(self, data, user, is_staff_or_admin):
        """Create a single category."""
        # Prepare data with user assignment
        logger.debug(
            f"Creating single category. User: {user.id}, Is staff/admin: {is_staff_or_admin}"
        )
        logger.debug(f"Raw data: {data}")
        prepared_data = self._prepare_category_data(data, user, is_staff_or_admin)
        logger.debug(f"Prepared data: {prepared_data}")

        # Validate and save
        serializer = self.get_serializer(
            data=prepared_data, context={"request": self.request}
        )

        # Log validation errors if any
        if not serializer.is_valid():
            logger.error(f"Serializer validation errors: {serializer.errors}")
            raise ValidationError(serializer.errors)

        serializer.is_valid(raise_exception=True)

        with db_transaction.atomic():
            category = serializer.save()

        logger.info(
            f"Category created successfully: id={category.id}, name='{category.name}', "
            f"user={'system' if is_staff_or_admin else user.id}, "
            f"is_system_category={category.is_system_category}"
        )

        # Double-check the category was saved
        try:
            saved_category = Category.objects.get(id=category.id)
            logger.debug(f"Category verified in DB: {saved_category}")
        except Category.DoesNotExist:
            logger.error(f"Category {category.id} was not saved to database!")

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def _bulk_create_categories(self, data_list, user, is_staff_or_admin):
        """Create multiple categories with proper error handling."""
        # Validate input
        if not isinstance(data_list, list):
            return Response(
                {"error": "Expected a list of categories."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Limit batch size
        MAX_BATCH_SIZE = settings.MAX_BATCH_SIZE
        if len(data_list) > MAX_BATCH_SIZE:
            return Response(
                {
                    "error": f"Cannot create more than {MAX_BATCH_SIZE} categories at once."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []

        with db_transaction.atomic():
            for idx, data in enumerate(data_list):
                try:
                    # Prepare data for each category
                    prepared_data = self._prepare_category_data(
                        data, user, is_staff_or_admin
                    )

                    # Validate
                    serializer = self.get_serializer(
                        data=prepared_data, context={"request": self.request}
                    )
                    serializer.is_valid(raise_exception=True)

                    # Save
                    category = serializer.save()
                    created.append(serializer.data)

                    logger.debug(f"Bulk create: created category {category.id}")

                except Exception as e:
                    errors.append({"index": idx, "error": str(e), "data": data})
                    logger.warning(f"Bulk create error at index {idx}: {e}")

        # Return appropriate response
        if errors:
            return Response(
                {
                    "created": created,
                    "errors": errors,
                    "message": f"Created {len(created)} categories, {len(errors)} failed.",
                },
                status=status.HTTP_207_MULTI_STATUS,
            )

        logger.info(
            f"Bulk create successful: created {len(created)} categories "
            f"for user {'system' if is_staff_or_admin else user.id}"
        )

        return Response(created, status=status.HTTP_201_CREATED)

    def _prepare_category_data(self, data, user, is_staff_or_admin):
        """Prepare category data with proper user assignment."""
        logger.debug(
            f"Preparing category data. User: {user.id}, Is staff/admin: {is_staff_or_admin}"
        )

        prepared_data = data.copy() if isinstance(data, dict) else {}
        logger.debug(f"Original data copy: {prepared_data}")

        # Determine category ownership
        if is_staff_or_admin:
            prepared_data["is_system_category"] = True
            prepared_data["user"] = None  # System categories have no user
            logger.debug("Setting as system category (staff/admin)")
        else:
            prepared_data["is_system_category"] = False
            prepared_data["user"] = user.id  # Regular users own their categories
            logger.debug(f"Setting as user category for user {user.id}")

        # Ensure is_active defaults to True
        if "is_active" not in prepared_data:
            prepared_data["is_active"] = True
            logger.debug("Setting default is_active=True")

        # Ensure required fields are present
        required_fields = ["name", "category_type"]
        for field in required_fields:
            if field not in prepared_data:
                logger.warning(f"Required field '{field}' not in prepared data")

        logger.debug(f"Final prepared data: {prepared_data}")
        return prepared_data

    def partial_update(self, request, *args, **kwargs):
        """
        Update specific fields of a category.

        Restrictions:
        - Regular users cannot modify system categories
        - Regular users cannot change is_system_category
        """
        try:
            instance = self.get_object()
            user = request.user

            # Check permissions
            self.check_object_permissions(request, instance)

            # Validate modification rights
            self._validate_update_permissions(instance, user, request.data)

            # Perform update
            serializer = self.get_serializer(
                instance, data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Category updated: id={updated_instance.id}, "
                f"updated_fields={list(request.data.keys())}, "
                f"by user={user.id}"
            )

            return Response(serializer.data)

        except (PermissionDenied, NotAuthenticated, NotFound, ValidationError) as e:
            raise e
        except Exception as e:
            logger.exception(f"Error updating category: {e}")
            return Response(
                {"error": "Failed to update category."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _validate_update_permissions(self, category, user, update_data):
        """Validate if user can update the category."""
        # Regular users cannot modify system categories
        if category.is_system_category and not (user.is_staff or user.is_superuser):
            raise PermissionDenied("Cannot modify system categories.")

        # Regular users cannot change is_system_category field
        if "is_system_category" in update_data and not (
            user.is_staff or user.is_superuser
        ):
            raise PermissionDenied("Cannot change system category status.")

    def destroy(self, request, *args, **kwargs):
        """
        Soft delete a category (set is_active=False).

        Restrictions:
        - Cannot delete categories with transactions
        - Cannot delete categories with active children
        - System categories can only be deleted by staff/admin
        """
        try:
            instance = self.get_object()
            user = request.user

            # Check permissions
            self.check_object_permissions(request, instance)

            # Validate deletion
            if not self._can_delete_category(instance, user):
                error_msg = self._get_delete_error_message(instance, user)
                logger.error(error_msg)
                return Response(
                    {"error": error_msg}, status=status.HTTP_400_BAD_REQUEST
                )

            # Soft delete
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])

            logger.info(
                f"Category soft deleted: name='{instance.name}', by user={user.id}"
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        except (PermissionDenied, NotAuthenticated, NotFound) as e:
            raise e
        except Exception as e:
            logger.exception(f"Error deleting category: {e}")
            return Response(
                {"error": "Failed to delete category."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _can_delete_category(self, category, user):
        """Check if category can be deleted."""
        # Check transaction count
        if category.transaction_count > 0:
            return False

        # Check for active children
        if category.children.filter(is_active=True).exists():
            return False

        # Check ownership
        if not category.is_system_category and category.user != user:
            return False

        # Check system category permissions
        if category.is_system_category and not (user.is_staff or user.is_superuser):
            return False

        return True

    def _get_delete_error_message(self, category, user):
        """Get appropriate error message for delete failure."""
        if category.transaction_count > 0:
            return "Cannot delete category with transactions."

        if category.children.filter(is_active=True).exists():
            return "Cannot delete category with active subcategories."

        if not category.is_system_category and category.user != user:
            return "Cannot delete another user's category."

        if category.is_system_category and not (user.is_staff or user.is_superuser):
            return "Cannot delete system category."

        return "Cannot delete category."

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
        try:
            user = request.user

            # Get base queryset
            if user.is_staff or user.is_superuser:
                queryset = Category.objects.all()
            else:
                queryset = Category.objects.filter(
                    models.Q(user=user) | models.Q(is_system_category=True)
                )

            # Apply filters
            filter_instance = CategoryFilter(request, queryset)
            filtered_queryset = filter_instance.apply_filters()

            # For tree view, we want to show only root categories by default
            # But allow filtering by parent_id if specified
            if not request.query_params.get("parent_id"):
                filtered_queryset = filtered_queryset.filter(parent__isnull=True)

            # Paginate and serialize
            page = self.paginate_queryset(filtered_queryset.order_by("name"))
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(filtered_queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error getting category tree: {e}")
            return Response(
                {"error": "Failed to retrieve category tree."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
        try:
            user = request.user

            # Base queryset for system categories
            queryset = Category.objects.all()
            if user.is_staff or user.is_superuser:
                queryset = queryset
            else:
                queryset = queryset.filter(is_active=True)

            # Apply filters
            filter_instance = CategoryFilter(request, queryset, is_system=True)
            filtered_queryset = filter_instance.apply_filters()

            # Paginate and serialize
            page = self.paginate_queryset(filtered_queryset.order_by("name"))
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(filtered_queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error getting system categories: {e}")
            return Response(
                {"error": "Failed to retrieve system categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
        try:
            user = request.user

            queryset = Category.objects.all()
            if user.is_staff or user.is_superuser:
                queryset = queryset
            else:
                queryset = queryset.filter(is_active=True)

            # Apply filters
            filter_instance = CategoryFilter(request, queryset, is_mine=True)
            filtered_queryset = filter_instance.apply_filters()

            # Paginate and serialize
            page = self.paginate_queryset(filtered_queryset.order_by("name"))
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(filtered_queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error getting user categories: {e}")
            return Response(
                {"error": "Failed to retrieve your categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
