from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Category, Currency


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    """
    Admin interface for Currency model.
    Organized with fieldsets and audit trail protection.
    """

    list_display = (
        "code",
        "name",
        "symbol",
        "exchange_rate",
        "is_active",
        "is_base_currency",
        "exchange_updated_at",
    )
    list_display_links = ("code", "name")
    list_filter = ("is_active", "is_base_currency", "exchange_source")
    search_fields = ("code", "name", "symbol")
    ordering = ("code",)

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
                )
            },
        ),
        (
            _("Exchange Logic"),
            {
                "classes": ("collapse",),
                "fields": (
                    "is_base_currency",
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

    def save_model(self, request, obj, form, change):
        """Log admin intervention on save."""
        if change:
            # If rate changed manually in admin, log it
            if "exchange_rate" in form.changed_data:
                obj.exchange_source = f"Admin: {request.user.email}"
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
