from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.db import transaction as db_transaction
from django.utils import timezone

from utils import loggings
from utils.paginations import CustomPageNumberPagination

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
    CategorySerializer,
    CategoryListSerializer,
    CategoryDetailSerializer,
    CategoryTreeSerializer,
    CategoryBulkUpdateSerializer,
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
                if is_active is not None:
                    queryset = queryset.filter(is_active=is_active.lower() == "true")

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

            logger.info(
                f"Currencies created by {request.user.role} {request.user.email}"
            )

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
        from .models import Account, Transaction

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
        from .models import Account, Transaction

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


class BaseCategoryViewSet(viewsets.GenericViewSet):
    """Base ViewSet with common category functionality."""

    permission_classes = [CategoryPermission]
    pagination_class = CustomPageNumberPagination

    def get_queryset(self):
        """
        Get categories based on user permissions.

        Rules:
        - Regular users: their categories + system categories
        - Staff/Admin: all categories
        """
        user = self.request.user

        if user.is_staff or user.is_superuser:
            # Staff/Admin can see everything
            return Category.objects.all()

        # Regular users see their categories + system categories
        return Category.objects.filter(
            models.Q(user=user) | models.Q(is_system_category=True)
        ).filter(is_active=True)

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        serializer_map = {
            "list": CategoryListSerializer,
            "retrieve": CategoryDetailSerializer,
            "tree": CategoryTreeSerializer,
            "bulk_update": CategoryBulkUpdateSerializer,
        }
        return serializer_map.get(self.action, CategorySerializer)


class CategoryViewSet(
    BaseCategoryViewSet,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
):
    """
    Category ViewSet for CRUD operations with role-based permissions.

    Permissions:
    - List/Retrieve: All authenticated users (their categories + system categories)
    - Create: All users (auto-assigns user/system based on role)
    - Update/Delete: Users can only modify their own categories
    - Staff/Admin: Can CRUD any category including system categories

    Business Rules:
    - Staff/Admin creating categories become system categories (user=None)
    - Regular users create personal categories (user=request.user)
    - System categories are read-only for regular users
    """

    queryset = Category.objects.all()
    lookup_field = "pk"

    @extend_schema(
        summary="List categories",
        description=(
            "List categories visible to the user. "
            "Regular users see their categories + system categories. "
            "Staff/Admin see all categories."
        ),
        parameters=[
            OpenApiParameter(
                name="search",
                description="Search by category name",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="category_type",
                description="Filter by category type (income/expense)",
                required=False,
                type=str,
                enum=["income", "expense"],
            ),
            OpenApiParameter(
                name="parent_id",
                description="Filter by parent category ID",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="include_inactive",
                description="Include inactive categories (staff/admin only)",
                required=False,
                type=bool,
            ),
        ],
    )
    def list(self, request, *args, **kwargs):
        """List categories with optional filtering."""
        try:
            queryset = self.get_queryset()
            user = request.user

            # Apply filters
            search_query = request.query_params.get("search")
            if search_query:
                queryset = queryset.filter(name__icontains=search_query)

            category_type = request.query_params.get("category_type")
            if category_type:
                queryset = queryset.filter(category_type=category_type)

            parent_id = request.query_params.get("parent_id")
            if parent_id:
                try:
                    queryset = queryset.filter(parent_id=parent_id)
                except ValueError:
                    logger.warning(f"Invalid parent_id filter: {parent_id}")

            # Staff/Admin can see inactive categories
            include_inactive = request.query_params.get("include_inactive")
            if include_inactive and (user.is_staff or user.is_superuser):
                if include_inactive.lower() == "true":
                    queryset = Category.objects.filter(
                        models.Q(user=user)
                        | models.Q(is_system_category=True)
                        | models.Q(user__isnull=False)
                    )
            else:
                # Regular users only see active categories
                if not (user.is_staff or user.is_superuser):
                    queryset = queryset.filter(is_active=True)

            # Order by name for consistency
            queryset = queryset.order_by("name")

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error listing categories: {e}")
            return Response(
                {"error": "An error occurred while retrieving categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create category",
        description=(
            "Create a new category. "
            "Staff/Admin create system categories (user=None). "
            "Regular users create personal categories (user=request.user)."
        ),
        request=CategorySerializer,
        responses={201: CategorySerializer},
    )
    def create(self, request, *args, **kwargs):
        """Create a new category with automatic user assignment."""
        try:
            user = request.user
            is_staff_or_admin = user.is_staff or user.is_superuser

            # Prepare data with user assignment
            data = request.data.copy()

            if is_staff_or_admin:
                # Staff/Admin create system categories
                data["is_system_category"] = True
                data["user"] = None
                logger.debug(f"Staff/Admin creating system category")
            else:
                # Regular users create personal categories
                data["is_system_category"] = False
                data["user"] = user.id
                logger.debug(f"Regular user creating personal category")

            serializer = self.get_serializer(data=data, context={"request": request})
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                category = serializer.save()

            logger.info(
                f"Category created: id={category.id}, "
                f"name='{category.name}', "
                f"user={'system' if category.is_system_category else user.id}, "
                f"type={category.category_type}"
            )

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Error creating category: {e}")
            return Response(
                {"error": "Failed to create category. Please check your data."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Bulk create categories",
        description="Create multiple categories at once.",
        request=CategorySerializer(many=True),
        responses={207: CategorySerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="bulk-create")
    def bulk_create(self, request):
        """Create multiple categories."""
        try:
            user = request.user
            is_staff_or_admin = user.is_staff or user.is_superuser

            # Prepare data for each category
            categories_data = request.data
            if not isinstance(categories_data, list):
                return Response(
                    {"error": "Expected a list of categories."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Limit batch size
            if len(categories_data) > 50:
                return Response(
                    {"error": "Cannot create more than 50 categories at once."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            created = []
            errors = []

            for idx, category_data in enumerate(categories_data):
                try:
                    # Prepare data with user assignment
                    data = category_data.copy()

                    if is_staff_or_admin:
                        data["is_system_category"] = True
                        data["user"] = None
                    else:
                        data["is_system_category"] = False
                        data["user"] = user.id

                    serializer = CategorySerializer(
                        data=data, context={"request": request}
                    )
                    serializer.is_valid(raise_exception=True)

                    with db_transaction.atomic():
                        category = serializer.save()

                    created.append(serializer.data)
                    logger.debug(f"Bulk create: created category {category.id}")

                except Exception as e:
                    errors.append(
                        {"index": idx, "error": str(e), "data": category_data}
                    )
                    logger.warning(f"Bulk create error at index {idx}: {e}")

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

        except Exception as e:
            logger.exception(f"Error in bulk create: {e}")
            return Response(
                {"error": "Failed to create categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Update category",
        description=(
            "Update a category. "
            "Regular users can only update their own categories. "
            "Staff/Admin can update any category."
        ),
        request=CategorySerializer,
        responses={200: CategorySerializer},
    )
    def update(self, request, *args, **kwargs):
        """Update category."""
        try:
            instance = self.get_object()
            user = request.user

            # Check if user can modify this category
            self.check_object_permissions(request, instance)

            # Prevent regular users from modifying system categories
            if instance.is_system_category and not (user.is_staff or user.is_superuser):
                return Response(
                    {"error": "Cannot modify system categories."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            serializer = self.get_serializer(
                instance, data=request.data, partial=False, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Category updated: id={updated_instance.id}, "
                f"name='{updated_instance.name}', "
                f"by user={user.id}"
            )

            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error updating category: {e}")
            return Response(
                {"error": "Failed to update category."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Partial update category",
        description="Update specific fields of a category.",
        request=CategorySerializer,
        responses={200: CategorySerializer},
    )
    def partial_update(self, request, *args, **kwargs):
        """Partial update category."""
        try:
            instance = self.get_object()
            user = request.user

            # Check permissions
            self.check_object_permissions(request, instance)

            # Prevent regular users from modifying system categories
            if instance.is_system_category and not (user.is_staff or user.is_superuser):
                return Response(
                    {"error": "Cannot modify system categories."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Prevent changing is_system_category for regular users
            if "is_system_category" in request.data and not (
                user.is_staff or user.is_superuser
            ):
                return Response(
                    {"error": "Cannot change system category status."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            serializer = self.get_serializer(
                instance, data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            with db_transaction.atomic():
                updated_instance = serializer.save()

            logger.info(
                f"Category partially updated: id={updated_instance.id}, "
                f"updated fields={list(request.data.keys())}, "
                f"by user={user.id}"
            )

            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error partially updating category: {e}")
            return Response(
                {"error": "Failed to update category."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(
        summary="Delete category",
        description=(
            "Soft delete a category (set is_active=False). "
            "Categories with transactions cannot be deleted. "
            "System categories can only be deleted by staff/admin."
        ),
        responses={204: OpenApiResponse(description="No Content")},
    )
    def destroy(self, request, *args, **kwargs):
        """Soft delete category."""
        try:
            instance = self.get_object()
            user = request.user

            # Check permissions
            self.check_object_permissions(request, instance)

            # Check if category can be deleted
            if not self._can_delete_category(instance, user):
                error_msg = self._get_delete_error_message(instance, user)
                return Response(
                    {"error": error_msg}, status=status.HTTP_400_BAD_REQUEST
                )

            # Soft delete (set is_active=False)
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])

            logger.info(
                f"Category soft deleted: id={instance.id}, "
                f"name='{instance.name}', "
                f"by user={user.id}"
            )

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            logger.exception(f"Error deleting category: {e}")
            return Response(
                {"error": "Failed to delete category."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _can_delete_category(self, category: Category, user) -> bool:
        """Check if category can be deleted."""
        # Staff/Admin can delete any category (except with transactions)
        if user.is_staff or user.is_superuser:
            return category.transaction_count == 0

        # Regular users can only delete their own categories
        if category.user != user:
            return False

        # Check for transactions
        if category.transaction_count > 0:
            return False

        # Check for active children
        if category.children.filter(is_active=True).exists():
            return False

        return True

    def _get_delete_error_message(self, category: Category, user) -> str:
        """Get appropriate error message for delete failure."""
        if category.transaction_count > 0:
            return "Cannot delete category with transactions."

        if category.children.filter(is_active=True).exists():
            return "Cannot delete category with active subcategories."

        if category.user != user and not (user.is_staff or user.is_superuser):
            return "Cannot delete another user's category."

        if category.is_system_category and not (user.is_staff or user.is_superuser):
            return "Cannot delete system category."

        return "Cannot delete category."

    @extend_schema(
        summary="Get category tree",
        description="Get hierarchical category tree for the user.",
        responses={200: CategoryTreeSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="tree")
    def tree(self, request):
        """Get hierarchical category tree."""
        try:
            user = request.user

            # Get root categories (no parent)
            if user.is_staff or user.is_superuser:
                # Staff/Admin see all root categories
                root_categories = Category.objects.filter(
                    parent__isnull=True, is_active=True
                )
            else:
                # Regular users see their root categories + system root categories
                root_categories = Category.objects.filter(
                    models.Q(parent__isnull=True)
                    & (models.Q(user=user) | models.Q(is_system_category=True))
                    & models.Q(is_active=True)
                )

            serializer = CategoryTreeSerializer(
                root_categories.order_by("name"),
                many=True,
                context={"request": request},
            )

            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error getting category tree: {e}")
            return Response(
                {"error": "Failed to retrieve category tree."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Bulk update categories",
        description="Bulk update categories (activate/deactivate/change parent). Staff/Admin only.",
        request=CategoryBulkUpdateSerializer,
        responses={200: CategoryBulkUpdateSerializer},
    )
    @action(detail=False, methods=["post"], url_path="bulk-update")
    def bulk_update(self, request):
        """Bulk update categories (staff/admin only)."""
        try:
            user = request.user

            # Only staff/admin can perform bulk operations
            if not (user.is_staff or user.is_superuser):
                return Response(
                    {"error": "Bulk operations require staff/admin privileges."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            serializer = CategoryBulkUpdateSerializer(
                data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)

            result = serializer.save()

            logger.info(
                f"Bulk update completed by {user.id}: "
                f"action={request.data.get('action')}, "
                f"successful={result.get('successful', 0)}, "
                f"failed={result.get('failed', 0)}"
            )

            return Response(result)

        except Exception as e:
            logger.exception(f"Error in bulk update: {e}")
            return Response(
                {"error": "Bulk update failed."}, status=status.HTTP_400_BAD_REQUEST
            )

    @extend_schema(
        summary="Get system categories",
        description="Get all system categories.",
        responses={200: CategoryListSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="system")
    def system_categories(self, request):
        """Get all system categories."""
        try:
            system_categories = Category.objects.filter(
                is_system_category=True, is_active=True
            ).order_by("name")

            serializer = CategoryListSerializer(
                system_categories, many=True, context={"request": request}
            )

            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error getting system categories: {e}")
            return Response(
                {"error": "Failed to retrieve system categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Get user categories",
        description="Get categories belonging to the current user.",
        responses={200: CategoryListSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="mine")
    def my_categories(self, request):
        """Get categories belonging to the current user."""
        try:
            user = request.user
            user_categories = Category.objects.filter(
                user=user, is_active=True
            ).order_by("name")

            serializer = CategoryListSerializer(
                user_categories, many=True, context={"request": request}
            )

            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error getting user categories: {e}")
            return Response(
                {"error": "Failed to retrieve your categories."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
