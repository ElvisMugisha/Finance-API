from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from utils import loggings
from utils.paginations import CustomPageNumberPagination
from .models import Currency
from .serializers import CurrencySerializer

# Initialize logger
logger = loggings.setup_logging()


class CurrencyListView(APIView):
    """
    API View for listing and creating currencies.
    """

    serializer_class = CurrencySerializer
    pagination_class = CustomPageNumberPagination

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        GET: Authenticated users.
        POST: Admin users only.
        """
        if self.request.method == "POST":
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    @extend_schema(
        summary="List available currencies",
        description="Retrieve a paginated list of all active currencies.",
        responses={
            200: CurrencySerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def get(self, request):
        """
        Handle GET request to list currencies.
        """
        logger.info(f"Currency list requested by {request.user.email}")

        try:
            queryset = Currency.objects.filter(is_active=True).order_by("code")

            # Apply search filter manually or use filter backend
            search_query = request.query_params.get("search", None)
            if search_query:
                queryset = queryset.filter(
                    code__icontains=search_query
                ) | queryset.filter(name__icontains=search_query)

            # Apply pagination
            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serializer = self.serializer_class(paginated_queryset, many=True)

            logger.info(
                f"Retrieved {len(serializer.data)} currencies (page {request.query_params.get('page', 1)})"
            )
            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            logger.exception(f"Error retrieving currency list: {str(e)}")
            return Response(
                {"error": "An error occurred while retrieving currencies."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        summary="Create currencies (Bulk supported)",
        description="Create one or multiple currencies. Only accessible by Admins/Staff.",
        request=CurrencySerializer(many=True),  # Indicates list support
        responses={
            201: CurrencySerializer(many=True),
            400: OpenApiResponse(description="Bad Request - Validation Error"),
            403: OpenApiResponse(description="Forbidden - Insufficient permissions"),
        },
    )
    def post(self, request):
        """
        Handle POST request to create currencies (supports bulk creation).
        Request body can be a single object or a list of objects.
        """
        logger.info(f"Currency creation requested by {request.user.email}")

        is_bulk = isinstance(request.data, list)

        serializer = self.serializer_class(data=request.data, many=is_bulk)

        if serializer.is_valid():
            try:
                serializer.save()
                count = len(serializer.data) if is_bulk else 1
                logger.info(f"Successfully created {count} currencies")
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.exception(f"Error creating currencies: {str(e)}")
                return Response(
                    {"error": "An error occurred while creating currencies."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        logger.warning(f"Currency creation validation failed: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CurrencyDetailView(APIView):
    """
    API View for retrieving, updating, and deleting a specific currency.
    """

    serializer_class = CurrencySerializer

    def get_permissions(self):
        """
        GET: Authenticated users.
        PUT/DELETE: Admin users only.
        """
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_object(self, pk):
        currency = get_object_or_404(Currency, pk=pk)
        return currency

    @extend_schema(
        summary="Retrieve currency details",
        description="Get detailed information about a specific currency.",
        responses={
            200: CurrencySerializer,
            404: OpenApiResponse(description="Not Found"),
        },
    )
    def get(self, request, pk):
        logger.info(f"Currency detail requested: {pk} by {request.user.email}")
        currency = self.get_object(pk)
        serializer = self.serializer_class(currency)
        return Response(serializer.data)

    @extend_schema(
        summary="Update currency",
        description="Update a currency's details partially. Admin only.",
        request=CurrencySerializer,
        responses={
            200: CurrencySerializer,
            400: OpenApiResponse(description="Bad Request"),
            403: OpenApiResponse(description="Forbidden"),
        },
    )
    def patch(self, request, pk):
        logger.info(
            f"Currency partial update requested: {pk} by admin {request.user.email}"
        )
        currency = self.get_object(pk)
        serializer = self.serializer_class(currency, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            logger.info(f"Currency {currency.code} updated")
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Delete currency",
        description="Delete a currency. Admin only.",
        responses={
            204: OpenApiResponse(description="No Content"),
            403: OpenApiResponse(description="Forbidden"),
        },
    )
    def delete(self, request, pk):
        logger.info(f"Currency deletion requested: {pk} by admin {request.user.email}")
        currency = self.get_object(pk)
        currency_code = currency.code
        currency.delete()
        logger.info(f"Currency {currency_code} deleted")
        return Response(status=status.HTTP_204_NO_CONTENT)
