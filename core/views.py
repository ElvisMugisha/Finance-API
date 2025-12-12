from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.response import Response

from utils import loggings
from utils.paginations import CustomPageNumberPagination
from utils.permissions import CategoryPermission, IsActiveAndVerified, IsAdminUser

from .models import Category, Currency
from .serializers import CategorySerializer, CurrencySerializer

# Initialize logger
logger = loggings.setup_logging()


class CurrencyViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Currency CRUD operations.

    Features:
    - List all active currencies (GET)
    - Retrieve a specific currency (GET)
    - Create single or multiple currencies (POST, Admin only)
    - Partial update (PATCH, Admin only)
    - Delete (soft delete optional, Admin only)
    """

    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer
    pagination_class = CustomPageNumberPagination
    lookup_field = "pk"

    def get_permissions(self):
        """Set permissions per method."""
        if self.action in ["create", "partial_update", "destroy", "update"]:
            return [IsAdminUser()]
        return [IsActiveAndVerified()]

    def get_queryset(self):
        """Filter only active currencies for non-admin users."""
        user = self.request.user
        qs = Currency.objects.all()
        if not (user.is_staff or user.is_superuser):
            qs = qs.filter(is_active=True)
        return qs.order_by("code")

    @extend_schema(
        summary="List currencies",
        description="Retrieve a paginated list of currencies visible to the user.",
        responses={200: CurrencySerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        """List currencies with optional search."""
        try:
            queryset = self.get_queryset()
            search_query = request.query_params.get("search")
            if search_query:
                queryset = queryset.filter(
                    code__icontains=search_query
                ) | queryset.filter(name__icontains=search_query)

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception("Error listing currencies: %s", str(e))
            return Response(
                {"error": "An error occurred while retrieving currencies."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create currencies (Bulk supported)",
        description="Create one or multiple currencies. Admin only.",
        request=CurrencySerializer(many=True),
        responses={201: CurrencySerializer(many=True)},
    )
    def create(self, request, *args, **kwargs):
        """Create one or multiple currencies."""
        try:
            is_bulk = isinstance(request.data, list)
            serializer = self.get_serializer(data=request.data, many=is_bulk)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            logger.info("Currencies created by admin %s", request.user.email)
            status_code = (
                status.HTTP_201_CREATED if not is_bulk else status.HTTP_207_MULTI_STATUS
            )
            return Response(serializer.data, status=status_code)
        except Exception as e:
            logger.exception("Error creating currencies: %s", str(e))
            return Response(
                {"error": "Failed to create currencies."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Partial update currency",
        description="Update one or more fields of a currency. Admin only.",
        request=CurrencySerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        """PATCH update currency."""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(
            "Currency '%s' updated by admin %s.", instance.code, request.user.email
        )
        return Response(serializer.data)

    @extend_schema(
        summary="Delete currency",
        description="Delete a currency. Admin only.",
        responses={204: OpenApiResponse(description="No Content")},
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a currency."""
        instance = self.get_object()
        code = instance.code
        instance.delete()
        logger.info("Currency '%s' deleted by admin %s.", code, request.user.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CategoryViewSet(viewsets.ModelViewSet):
    """
    Category ViewSet for CRUD operations.

    Supports:
    - List / Retrieve / Patch / Soft-delete
    - Bulk creation via POST with list of categories
    - System categories: only editable by staff/superusers
    - Regular users: only editable for own categories
    """

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = CustomPageNumberPagination
    permission_classes = [CategoryPermission]
    lookup_field = "pk"  # UUID primary key

    def get_queryset(self):
        """Filter categories based on user permissions."""
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return Category.objects.all()
        return Category.objects.filter(user=user, is_active=True)

    @extend_schema(
        summary="List categories",
        description="List categories visible to the user.",
    )
    def list(self, request, *args, **kwargs):
        """List categories with pagination."""
        queryset = self.get_queryset().order_by("name")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Create category / bulk creation",
        description="Create a new category or multiple categories at once.",
        request=CategorySerializer,
    )
    def create(self, request, *args, **kwargs):
        """Create one or more categories."""
        user = request.user
        data = request.data

        # Support both single dict and list
        is_bulk = isinstance(data, list)
        data_list = data if is_bulk else [data]

        created_categories = []
        errors = []

        for idx, item in enumerate(data_list):
            serializer = self.get_serializer(data=item, context={"request": request})
            try:
                serializer.is_valid(raise_exception=True)
                category = serializer.save(user=user)
                created_categories.append(serializer.data)
                logger.info(
                    "Category created: '%s' by user '%s'.",
                    category.name,
                    user.id,
                )
            except Exception as e:
                logger.error("Error creating category at index %s: %s", idx, str(e))
                errors.append({"index": idx, "error": str(e)})

        if errors:
            return Response(
                {"created": created_categories, "errors": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        status_code = (
            status.HTTP_201_CREATED if not is_bulk else status.HTTP_207_MULTI_STATUS
        )
        return Response(created_categories, status=status_code)

    @extend_schema(
        summary="Partial update category (PATCH)",
        description="Update mutable fields of a category. System categories are protected for regular users.",
    )
    def partial_update(self, request, *args, **kwargs):
        """PATCH update category."""
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(
            "Category '%s' (%s) updated by user '%s'.",
            instance.name,
            instance.id,
            request.user.id,
        )
        return Response(serializer.data)

    @extend_schema(
        summary="Soft-delete category",
        description="Soft-delete (deactivate) a category by setting is_active=False.",
    )
    def destroy(self, request, *args, **kwargs):
        """Soft-delete a category."""
        instance = self.get_object()
        user = request.user

        if instance.is_system_category and not (user.is_staff or user.is_superuser):
            logger.warning(
                "User '%s' attempted to delete system category '%s'.",
                user.id,
                instance.id,
            )
            return Response(
                {"detail": "Cannot delete system category."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if instance.user != user and not (user.is_staff or user.is_superuser):
            logger.warning(
                "User '%s' attempted to delete another user's category '%s'.",
                user.id,
                instance.id,
            )
            return Response(
                {"detail": "Cannot delete another user's category."},
                status=status.HTTP_403_FORBIDDEN,
            )

        instance.is_active = False
        instance.save()
        logger.info(
            "Category '%s' (%s) soft-deleted by user '%s'.",
            instance.name,
            instance.id,
            user.id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
