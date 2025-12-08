from urllib.parse import urlparse, parse_qs, urlencode
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound

from utils.loggings import setup_logging

# Initialize logger
logger = setup_logging()


class CustomPageNumberPagination(PageNumberPagination):
    """
    Custom pagination class that extends Django REST Framework's PageNumberPagination.

    - Dynamic page size (?page_size=)
    - Configurable per-page options
    - Absolute/relative link toggle
    - Structured error handling
    - Enriched metadata (first/last/item_range/etc.)
    """

    # Allow clients to specify page size via query parameter (e.g., ?page_size=10)
    page_size_query_param = "page_size"
    max_page_size = 100

    # toggle absolute vs relative URLs
    use_absolute_links = getattr(settings, "PAGINATION_ABSOLUTE_LINKS", True)

    # expose available per_page options (override in settings if needed)
    per_page_options = getattr(
        settings, "PAGINATION_PAGE_SIZE_OPTIONS", [5, 10, 20, 50, 100]
    )

    def get_page_size(self, request):
        """Validate and enforce page size."""
        try:
            page_size = super().get_page_size(request)
            if page_size is None:
                page_size = self.page_size or 50  # fallback default

            # Validate the page size
            if page_size <= 0:
                logger.error(
                    "Invalid page size requested: %s. Must be positive.", page_size
                )
                raise ValidationError(
                    {"page_size": "Page size must be a positive integer."}
                )

            if self.max_page_size and page_size > self.max_page_size:
                logger.warning(
                    "Requested page size %s exceeds max %s. Using max_page_size.",
                    page_size,
                    self.max_page_size,
                )
                return self.max_page_size

            return page_size

        except ValueError as e:
            logger.error("Error parsing page size: %s", str(e))
            raise ValidationError(
                {"page_size": "Invalid format. Must be a positive integer."}
            )

    def _build_link(self, page_number: int):
        """
        Helper to build absolute or relative pagination links while preserving query params.

        - Ensures robust error handling and logging.
        - Returns None if a safe link cannot be generated.
        """
        try:
            if not hasattr(self, "request") or self.request is None:
                logger.error("Pagination link build failed: request object is missing.")
                return None

            url = self.request.build_absolute_uri()
            if not url:
                logger.error(
                    "Pagination link build failed: could not build absolute URI."
                )
                return None

            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)

            # Force page number into params
            query_params["page"] = [str(page_number)]

            # Preserve explicit page_size if paginator is initialized
            if hasattr(self, "page") and hasattr(self.page, "paginator"):
                if self.page_size_query_param:
                    query_params[self.page_size_query_param] = [
                        str(getattr(self.page.paginator, "per_page", ""))
                    ]

            else:
                logger.warning(
                    "Paginator not fully initialized when building link for page=%s",
                    page_number,
                )

            query_string = urlencode(query_params, doseq=True)
            link = f"{parsed_url.path}?{query_string}"

            final_link = (
                f"{parsed_url.scheme}://{parsed_url.netloc}{link}"
                if getattr(self, "use_absolute_links", False)
                else link
            )

            logger.debug(
                "Built pagination link for page=%s: %s", page_number, final_link
            )
            return final_link

        except Exception as e:
            logger.exception(
                "Unhandled error building pagination link for page=%s", page_number
            )
            return None

    def get_next_link(self):
        """
        Get the link for the next page.
        """
        try:
            return (
                self._build_link(self.page.next_page_number())
                if self.page.has_next()
                else None
            )
        except Exception as e:
            logger.error("Error generating next link: %s", str(e))
            return None

    def get_previous_link(self):
        """
        Get the link for the previous page.
        """
        try:
            return (
                self._build_link(self.page.previous_page_number())
                if self.page.has_previous()
                else None
            )
        except Exception as e:
            logger.error("Error generating previous link: %s", str(e))
            return None

    def get_first_link(self):
        """
        Get the link for the first page.
        """
        try:
            return None if self.page.number == 1 else self._build_link(1)
        except Exception as e:
            logger.error("Error generating first link: %s", str(e))
            return None

    def get_last_link(self):
        """
        Get the link for the last page.
        """
        try:
            return (
                None
                if self.page.number == self.page.paginator.num_pages
                else self._build_link(self.page.paginator.num_pages)
            )
        except Exception as e:
            logger.error("Error generating last link: %s", str(e))
            return None

    def paginate_queryset(self, queryset, request, view=None):
        """
        Override to gracefully handle out-of-range pages.
        """
        try:
            return super().paginate_queryset(queryset, request, view=view)
        except NotFound:
            max_page = self.page.paginator.num_pages if hasattr(self, "page") else None
            raise NotFound(
                {
                    "error": "Page out of range.",
                    "max_page": max_page,
                }
            )

    def get_paginated_response(self, data):
        """Return structured response with enriched pagination metadata."""
        try:
            # Verify that pagination attributes are available
            if not hasattr(self, "page") or not hasattr(self.page, "paginator"):
                logger.error("Pagination attributes not properly initialized.")
                raise ImproperlyConfigured("Pagination not initialized correctly.")

            start_index = self.page.start_index()
            end_index = self.page.end_index()
            total_items = self.page.paginator.count

            response_data = {
                "count": total_items,
                "num_of_pages": self.page.paginator.num_pages,
                "current_page": self.page.number,
                "page_size": self.page.paginator.per_page,
                "has_next": self.page.has_next(),
                "has_previous": self.page.has_previous(),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "first": self.get_first_link(),
                "last": self.get_last_link(),
                "item_range": f"Items {start_index}-{end_index} of {total_items}",
                "per_page_options": self.per_page_options,
                "data": data,
            }

            # Log successful pagination response
            logger.debug(
                "Generated paginated response: page=%s, page_size=%s, total_items=%s",
                self.page.number,
                self.page.paginator.per_page,
                total_items,
            )

            return Response(response_data)

        except Exception as e:
            logger.error("Error generating paginated response: %s", str(e))
            raise
