from django.contrib import admin

from .models import Passcode, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "is_active",
        "is_verified",
        "is_staff",
        "is_superuser",
    ]
    list_display_links = ["username", "email"]
    list_filter = ["is_active", "is_verified", "is_staff", "is_superuser"]
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering = ["-created_at"]


@admin.register(Passcode)
class PasscodeAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "code",
        "code_type",
        "created_at",
        "expires_at",
        "is_used",
    ]
    list_display_links = ["code", "user"]
    list_filter = ["code_type", "is_used"]
    search_fields = ["user", "code", "code_type"]
    ordering = ["-created_at"]
