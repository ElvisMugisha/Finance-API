from django.conf import settings
from django.contrib import admin, messages
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from utils import loggings

from . import exchange_rates
from .models import Category, Currency

logger = loggings.setup_logging()


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    """
    Admin interface for Currency model.
    Includes custom actions for real-time exchange rate updates.
    """

    list_display = (
        "code",
        "name",
        "symbol",
        "safe_exchange_rate",
        "exchange_source",
        "exchange_updated_at",
        "is_active",
        "is_base_currency",
    )
    list_display_links = ("code", "name")
    list_filter = ("is_active", "is_base_currency", "exchange_source")
    search_fields = ("code", "name", "symbol")
    ordering = ("code",)
    actions = ["update_exchange_rates_action", "force_refresh_exchange_rates"]

    readonly_fields = (
        "id",
        "exchange_updated_at",
        "created_at",
        "updated_at",
        "historical_rates_display",
    )

    fieldsets = (
        (
            _("Core Information"),
            {
                "fields": (
                    "id",
                    "code",
                    "name",
                    "symbol",
                    "decimal_places",
                    "is_active",
                    "is_base_currency",
                )
            },
        ),
        (
            _("Exchange Logic"),
            {
                "fields": (
                    "exchange_rate",
                    "exchange_source",
                    "exchange_updated_at",
                ),
            },
        ),
        (
            _("History & Audit"),
            {
                "classes": ("collapse",),
                "fields": (
                    "historical_rates_display",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.display(description=_("Exchange Rate"))
    def safe_exchange_rate(self, obj):
        """Display exchange rate or dash if null."""
        return obj.exchange_rate if obj.exchange_rate else "—"

    @admin.display(description=_("Recent History"))
    def historical_rates_display(self, obj):
        """Format historical rates for display in admin."""
        if not obj.historical_rates:
            return _("No history recorded.")
        # Show last 5 entries
        history = obj.historical_rates[-5:]
        lines = []
        for h in reversed(history):
            lines.append(
                f"{h.get('date', '')}: {h.get('rate', '')} ({h.get('source', 'N/A')})"
            )
        return "\n".join(lines)

    @admin.action(description=_("Update Exchange Rates from API"))
    def update_exchange_rates_action(self, request, queryset):
        """
        Manually trigger exchange rate update from configured APIs.
        """
        try:
            logger.info(f"Admin '{request.user}' triggered exchange rate update.")
            result = exchange_rates.update_exchange_rates()

            updated = ", ".join(result["updated"])
            skipped = ", ".join(result["skipped"])
            errors = "; ".join(result["errors"])

            if result["updated"]:
                self.message_user(
                    request,
                    f"Successfully updated: {updated}",
                    level=messages.SUCCESS,
                )

            if result["skipped"]:
                self.message_user(
                    request,
                    f"Skipped (base or not found): {skipped}",
                    level=messages.WARNING,
                )

            if result["errors"]:
                self.message_user(
                    request,
                    f"Errors occurred: {errors}",
                    level=messages.ERROR,
                )

        except Exception as e:
            logger.exception("Admin-triggered exchange update failed.")
            self.message_user(
                request,
                f"Exchange rate update failed: {str(e)}",
                level=messages.ERROR,
            )

    @admin.action(description=_("Force Refresh (Clear Cache)"))
    def force_refresh_exchange_rates(self, request, queryset):
        """
        Clear exchange rate cache and fetch fresh data.
        """
        try:
            cache_key = f"exchange_rates_response_{settings.BASE_CURRENCY}"
            cache.delete(cache_key)
            logger.info("Cleared exchange rate cache per admin request.")

            # Re-run update
            return self.update_exchange_rates_action(request, queryset)
        except Exception as e:
            self.message_user(
                request,
                f"Error refreshing cache: {str(e)}",
                level=messages.ERROR,
            )

    def save_model(self, request, obj, form, change):
        """Log manual admin changes to source."""
        if change and "exchange_rate" in form.changed_data:
            obj.exchange_source = f"Admin: {request.user.email}"
            obj.exchange_updated_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """
    Admin interface for Category model.
    Organized with fieldsets and usage statistics.
    """

    list_display = (
        "indented_name",
        "category_type",
        "user_display",
        "is_system_category",
        "transaction_count",
        "is_active",
        "updated_at",
    )
    list_display_links = ("indented_name",)
    list_filter = (
        "is_system_category",
        "category_type",
        "is_active",
        "created_at",
    )
    search_fields = ("name", "user__email", "user__first_name", "user__last_name")
    raw_id_fields = ("user", "parent")
    ordering = ("name",)

    readonly_fields = (
        "id",
        "transaction_count",
        "last_used_at",
        "created_at",
        "updated_at",
        "full_path_display",
    )

    fieldsets = (
        (
            _("Core Information"),
            {
                "fields": (
                    "id",
                    "name",
                    "category_type",
                    "description",
                    "is_active",
                )
            },
        ),
        (
            _("Hierarchy & Ownership"),
            {
                "classes": ("collapse",),
                "fields": (
                    "parent",
                    "full_path_display",
                    "user",
                    "is_system_category",
                ),
            },
        ),
        (
            _("Statistics & Audit"),
            {
                "classes": ("collapse",),
                "fields": (
                    "transaction_count",
                    "last_used_at",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.display(description=_("Name"))
    def indented_name(self, obj):
        """Display name with indentation based on depth."""
        depth = len(obj.get_ancestors())
        return f"{'—' * depth} {obj.name}"

    @admin.display(description=_("User"))
    def user_display(self, obj):
        """Display owner or [System]."""
        if obj.is_system_category:
            return _("[System]")
        return obj.user.email if obj.user else "-"

    @admin.display(description=_("Full Path"))
    def full_path_display(self, obj):
        """Display full hierarchical path."""
        return obj.full_path

    def save_model(self, request, obj, form, change):
        """Log administration actions."""
        if not change and not obj.user and not obj.is_system_category:
            # If creating a new category without user, default to system
            obj.is_system_category = True

        super().save_model(request, obj, form, change)
