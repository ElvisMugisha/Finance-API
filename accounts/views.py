from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.db.models import ProtectedError

from utils import loggings
from utils.paginations import CustomPageNumberPagination
from .models import Account
from .serializers import AccountSerializer

logger = loggings.setup_logging()


class AccountListCreateView(APIView):
    """
    API View for listing and creating user accounts.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AccountSerializer
    pagination_class = CustomPageNumberPagination

    @extend_schema(
        summary="List user accounts",
        description="Retrieve a paginated list of accounts belonging to the authenticated user.",
        responses={
            200: AccountSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def get(self, request):
        """
        List all accounts for the current user.
        """
        logger.info(f"Account list requested by {request.user.email}")

        try:
            queryset = Account.objects.filter(user=request.user).select_related(
                "currency"
            )

            # Filtering and Ordering (manual implementation as APIView doesn't auto-use FilterBackends)
            is_active = request.query_params.get("is_active")
            if is_active is not None:
                active_bool = is_active.lower() == "true"
                queryset = queryset.filter(is_active=active_bool)

            # Pagination
            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serializer = self.serializer_class(paginated_queryset, many=True)

            logger.info(f"Retrieved {len(serializer.data)} accounts")
            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            logger.exception(f"Error listing accounts: {e}")
            return Response(
                {"error": "Failed to retrieve accounts."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create new account",
        description="Create a new financial account for the authenticated user.",
        request=AccountSerializer,
        responses={
            201: AccountSerializer,
            400: OpenApiResponse(description="Validation Error"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Create a new account.
        """
        logger.info(f"Account creation requested by {request.user.email}")

        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )

        if serializer.is_valid():
            try:
                serializer.save()
                logger.info(f"Account created successfully for {request.user.email}")
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.exception(f"Error creation account: {e}")
                return Response(
                    {"error": "Failed to create account. Please try again."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        logger.warning(f"Account creation validation failed: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AccountDetailView(APIView):
    """
    API View for retrieving, updating, and deleting a specific account.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AccountSerializer

    def get_object(self, user, pk):
        """
        Helper to get object safely ensuring ownership.
        """
        obj = get_object_or_404(
            Account.objects.select_related("currency"), id=pk, user=user
        )
        return obj

    @extend_schema(
        summary="Get account details",
        description="Retrieve details of a specific account.",
        responses={
            200: AccountSerializer,
            404: OpenApiResponse(description="Not Found"),
        },
    )
    def get(self, request, pk):
        logger.info(f"Account detail requested: {pk} by {request.user.email}")
        account = self.get_object(request.user, pk)
        serializer = self.serializer_class(account)
        return Response(serializer.data)

    @extend_schema(
        summary="Update account",
        description="Update account details (e.g. name, type, initial_balance). Support partial updates.",
        request=AccountSerializer,
        responses={
            200: AccountSerializer,
            400: OpenApiResponse(description="Validation Error"),
            404: OpenApiResponse(description="Not Found"),
        },
    )
    def patch(self, request, pk):
        """
        Partial update of an account.
        """
        logger.info(f"Account update requested: {pk} by {request.user.email}")
        account = self.get_object(request.user, pk)

        serializer = self.serializer_class(
            account, data=request.data, partial=True, context={"request": request}
        )

        if serializer.is_valid():
            try:
                serializer.save()
                return Response(serializer.data)
            except Exception as e:
                logger.exception(f"Error updating account: {e}")
                return Response(
                    {"error": "Failed to update account."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Delete account",
        description="Delete an account. Fails if account has dependent data (transactions) preventing deletion.",
        responses={
            204: OpenApiResponse(description="No Content"),
            400: OpenApiResponse(
                description="Cannot delete account with existing transactions"
            ),
            404: OpenApiResponse(description="Not Found"),
        },
    )
    def delete(self, request, pk):
        logger.info(f"Account deletion requested: {pk} by {request.user.email}")
        account = self.get_object(request.user, pk)

        try:
            account.delete()
            logger.info(f"Account {pk} deleted successfully")
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            logger.warning(
                f"Cannot delete account {pk} due to protected references (transactions?)"
            )
            return Response(
                {
                    "error": "Cannot delete this account because it has related records (e.g. transactions). Archive it instead."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception(f"Error deleting account: {e}")
            return Response(
                {"error": "Failed to delete account."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
