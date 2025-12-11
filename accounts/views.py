from django.db.models import ProtectedError
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets, filters
from rest_framework.response import Response
from rest_framework.views import APIView

from utils import loggings
from utils.paginations import CustomPageNumberPagination
from utils.permissions import IsActiveAndVerified, IsOwnerOrAdmin

from .models import Account, Transaction
from .serializers import AccountSerializer, TransactionSerializer

logger = loggings.setup_logging()


class AccountViewSet(viewsets.ModelViewSet):
    """
    Account ViewSet for CRUD operations.

    Supports:
    - List / Retrieve / Patch / Soft-delete
    - Pagination, filtering, and ordering
    - Only account owner or admin can access
    """

    queryset = Account.objects.all()
    serializer_class = AccountSerializer
    permission_classes = [IsOwnerOrAdmin]
    pagination_class = CustomPageNumberPagination
    lookup_field = "id"  # UUID primary key

    def get_queryset(self):
        """Filter accounts based on user permissions."""
        user = self.request.user
        queryset = Account.objects.select_related("currency")
        if user.is_superuser or user.is_staff:
            return queryset
        return queryset.filter(user=user)

    @extend_schema(
        summary="List accounts",
        description="Retrieve paginated accounts for the authenticated user.",
    )
    def list(self, request, *args, **kwargs):
        """List accounts with optional filtering by active status."""
        try:
            queryset = self.get_queryset()
            queryset = queryset.order_by("-is_primary", "-created_at")

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)

            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)

        except Exception as e:
            logger.exception(f"Error listing accounts: {e}")
            return Response(
                {"error": "Failed to retrieve accounts."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create account",
        description="Create a new financial account for the authenticated user.",
        request=AccountSerializer,
        responses={
            201: AccountSerializer,
            400: OpenApiResponse(description="Validation Error"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new account."""
        try:
            serializer = self.get_serializer(
                data=request.data, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)
            serializer.save(user=request.user)
            logger.info(f"Account created successfully: {serializer.data}")
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.exception(f"Error creating account: {e}")
            return Response(
                {"error": "Failed to create account."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Partial update account",
        description="Update mutable fields of an account using PATCH.",
        request=AccountSerializer,
    )
    def partial_update(self, request, *args, **kwargs):
        """PATCH update account."""
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        logger.info(
            f"Account '{instance.name}' ({instance.id}) updated by user {request.user.id}."
        )
        return Response(serializer.data)

    @extend_schema(
        summary="Soft-delete account",
        description="Soft-delete (deactivate) an account.",
    )
    def destroy(self, request, *args, **kwargs):
        """Soft-delete (deactivate) an account."""
        instance = self.get_object()
        try:
            instance.is_active = False
            instance.save()
            logger.info(
                f"Account '{instance.name}' ({instance.id}) soft-deleted by user {request.user.id}."
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            logger.exception(f"Error deleting account: {e}")
            return Response(
                {"error": "Failed to delete account."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TransactionViewSet(viewsets.ModelViewSet):
    """
    Transaction ViewSet for CRUD operations.

    Supports:
    - List / Retrieve / Create / Patch (update-only) / Soft-delete
    - Owner-only access (admins override)
    - Filtering, searching, ordering, pagination

    PUT is DISABLED → only PATCH updates are allowed.
    """

    queryset = Transaction.objects.select_related("user", "account", "category").all()

    serializer_class = TransactionSerializer
    pagination_class = CustomPageNumberPagination
    permission_classes = [IsOwnerOrAdmin]
    lookup_field = "pk"  # UUID

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]

    filterset_fields = [
        "transaction_type",
        "category",
        "account",
        "status",
        "currency",
        "is_recurring",
        "is_transfer",
    ]

    search_fields = ["name", "description", "notes", "tags"]
    ordering_fields = ["transaction_date", "amount", "created_at"]

    def get_queryset(self):
        """Return transactions based on user permissions."""
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return Transaction.objects.all()
        return Transaction.objects.filter(user=user)

    @extend_schema(
        summary="List transactions",
        description="List all transactions accessible to the authenticated user.",
    )
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset().order_by("-transaction_date")
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Create transaction",
        description="Create a new transaction for the authenticated user.",
        request=TransactionSerializer,
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        transaction_obj = serializer.save(user=request.user)

        logger.info(
            "Transaction created: '%s' (%s) by user '%s'.",
            transaction_obj.name,
            transaction_obj.id,
            request.user.id,
        )

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Partial update transaction (PATCH)",
        description="Update mutable transaction fields. Owner-only except admin.",
    )
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()

        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        logger.info(
            "Transaction '%s' (%s) updated by user '%s'.",
            instance.name,
            instance.id,
            request.user.id,
        )

        return Response(serializer.data)

    @extend_schema(
        summary="Delete transaction",
        description="Delete a transaction. Owner-only except admin.",
    )
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user

        instance.delete()

        logger.info(
            "Transaction '%s' (%s) deleted by user '%s'.",
            instance.name,
            instance.id,
            user.id,
        )

        return Response(status=status.HTTP_204_NO_CONTENT)
