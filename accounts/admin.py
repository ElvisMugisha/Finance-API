from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    Account,
    Budget,
    FinancialGoal,
    RecurringTransaction,
    Report,
    Transaction,
)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user_email",
        "account_type",
        "currency_code",
        "current_balance",
        "is_primary",
        "is_active",
        "created_at",
    )
    list_display_links = ("name",)
    list_filter = ("account_type", "is_active", "is_primary", "currency")
    search_fields = ("name", "user__email", "user__username", "account_number")
    ordering = ("-created_at",)
    autocomplete_fields = ["user", "currency"]
    readonly_fields = ("current_balance", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("user", "name", "account_type", "currency")}),
        (
            _("Details"),
            {
                "fields": (
                    "account_number",
                    "bank_name",
                    "bank_code",
                    "initial_balance",
                    "current_balance",
                )
            },
        ),
        (
            _("Status & Settings"),
            {"fields": ("is_primary", "is_active", "is_locked", "institution_data")},
        ),
        (
            _("Timestamps"),
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = _("User")

    def currency_code(self, obj):
        return obj.currency.code

    currency_code.short_description = _("Currency")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user_email",
        "account",
        "amount",
        "transaction_type",
        "status",
        "transaction_date",
        "is_transfer",
    )
    list_filter = ("transaction_type", "status", "is_transfer", "transaction_date")
    search_fields = (
        "name",
        "description",
        "user__email",
        "merchant",
        "reference_number",
    )
    ordering = ("-transaction_date", "-created_at")
    autocomplete_fields = [
        "user",
        "account",
        "category",
        "original_currency",
        "transfer_account",
    ]
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("user", "account", "name", "transaction_type", "status")}),
        (
            _("Amount & Category"),
            {
                "fields": (
                    "amount",
                    "category",
                    "original_amount",
                    "original_currency",
                    "exchange_rate",
                )
            },
        ),
        (
            _("Details"),
            {
                "fields": (
                    "description",
                    "merchant",
                    "reference_number",
                    "transaction_date",
                )
            },
        ),
        (
            _("Transfer Info"),
            {"fields": ("is_transfer", "transfer_account", "transfer_reference")},
        ),
        (_("Metadata"), {"fields": ("tags", "is_recurring", "recurrence_metadata")}),
        (
            _("Timestamps"),
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = _("User")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user_email",
        "budget_type",
        "total_budget",
        "total_spent",
        "period_type",
        "is_active",
    )
    list_filter = ("budget_type", "period_type", "is_active")
    search_fields = ("name", "user__email")
    autocomplete_fields = ["user", "category", "currency"]
    readonly_fields = ("total_spent", "total_remaining", "created_at", "updated_at")

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = _("User")


@admin.register(FinancialGoal)
class FinancialGoalAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user_email",
        "goal_type",
        "target_amount",
        "current_amount",
        "progress_percentage",
        "target_date",
        "is_achieved",
    )
    list_filter = ("goal_type", "priority", "is_achieved", "is_active")
    search_fields = ("name", "user__email")
    autocomplete_fields = ["user", "currency", "linked_account"]
    readonly_fields = (
        "progress_percentage",
        "months_remaining",
        "created_at",
        "updated_at",
    )

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = _("User")


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "report_name",
        "user_email",
        "report_type",
        "status",
        "period_start",
        "period_end",
        "created_at",
    )
    list_filter = ("report_type", "status", "created_at")
    search_fields = ("report_name", "user__email")
    autocomplete_fields = ["user"]
    readonly_fields = ("generated_at", "created_at", "updated_at")

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = _("User")


@admin.register(RecurringTransaction)
class RecurringTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user_email",
        "amount",
        "frequency",
        "next_due_date",
        "is_active",
        "auto_create",
    )
    list_filter = ("frequency", "transaction_type", "is_active", "auto_create")
    search_fields = ("name", "user__email")
    autocomplete_fields = ["user", "account", "category"]

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = _("User")
