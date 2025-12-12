from django.contrib import admin

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "user_email",
        "account_type",
        "currency_code",
        "current_balance",
        "is_primary",
        "is_active",
    )
    list_display_links = ("id", "name")
    list_filter = ("account_type", "is_active", "is_primary", "currency")
    search_fields = ("name", "user__email", "user__username", "account_number")
    ordering = ("-created_at",)
    autocomplete_fields = ["user", "currency"]
    readonly_fields = ("current_balance", "created_at", "updated_at")

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = "User"

    def currency_code(self, obj):
        return obj.currency.code

    currency_code.short_description = "Currency"
