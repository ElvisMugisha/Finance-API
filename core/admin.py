from django.contrib import admin

from .models import Currency


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "exchange_rate", "is_active")
    list_filter = ("is_active", "exchange_source")
    search_fields = ("code", "name")
    ordering = ("code",)
