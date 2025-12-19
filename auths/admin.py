from django.contrib import admin

from .models import DeviceSession, Passcode, Profile, User, UserLoginAudit


class ProfileInline(admin.StackedInline):
    """
    Inline profile shown within the User admin page.

    This allows administrators to view and edit user profile data
    directly from the User admin without navigating elsewhere.
    """

    model = Profile
    extra = 0

    fieldsets = (
        (
            "Personal Information",
            {
                "fields": (
                    "bio",
                    "phone_number",
                    "date_of_birth",
                    "gender",
                    "profile_picture",
                )
            },
        ),
        (
            "Professional & Financial",
            {
                "fields": (
                    "occupation",
                    "annual_income",
                    "currency_preference",
                )
            },
        ),
        (
            "Preferences",
            {
                "fields": (
                    "notification_preferences",
                    "privacy_settings",
                )
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    readonly_fields = ("created_at", "updated_at")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """
    Admin configuration for the User model.
    """

    list_display = [
        "email",
        "username",
        "first_name",
        "last_name",
        "is_verified",
        "is_active",
        "is_staff",
        "is_superuser",
        "created_at",
    ]
    list_display_links = ("email", "username")
    list_filter = (
        "is_active",
        "is_verified",
        "is_staff",
        "is_superuser",
    )
    search_fields = ["username", "email", "first_name", "last_name"]
    ordering = ["-created_at"]
    readonly_fields = (
        "id",
        "last_login",
        "last_activity_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Identity",
            {
                "fields": (
                    "id",
                    "email",
                    "username",
                    "password",
                )
            },
        ),
        (
            "Personal Info",
            {
                "fields": (
                    "first_name",
                    "middle_name",
                    "last_name",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_verified",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Premium Status",
            {
                "fields": (
                    "is_premium",
                    "premium_expires",
                )
            },
        ),
        (
            "Activity",
            {
                "fields": (
                    "last_login",
                    "last_activity_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    inlines = [ProfileInline]


@admin.register(Passcode)
class PasscodeAdmin(admin.ModelAdmin):
    """
    Admin configuration for passcodes (OTP).
    """

    list_display = (
        "user",
        "code",
        "code_type",
        "is_used",
        "expires_at",
        "created_at",
    )
    list_display_links = ["code", "user"]
    list_filter = ["code_type", "is_used"]
    search_fields = ["user__email", "user__username", "code"]
    ready_only_fields = ["id", "created_at"]
    ordering = ["-created_at"]


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    """
    Admin configuration for DeviceSession.
    """

    list_display = (
        "user",
        "ip_address",
        "device",
        "user_agent",
        "created_at",
        "last_activity",
        "is_active",
    )
    list_display_links = ["user", "ip_address"]
    list_filter = ["is_active", "user_agent"]
    search_fields = ["user__email", "user__username", "ip_address", "device"]
    ready_only_fields = ["id", "created_at", "last_activity"]
    ordering = ["-created_at"]


@admin.register(UserLoginAudit)
class UserLoginAuditAdmin(admin.ModelAdmin):
    """
    Admin configuration for UserLoginAudit.
    """

    list_display = (
        "user",
        "email",
        "ip_address",
        "device",
        "user_agent",
        "status",
        "timestamp",
        "failure_reason",
    )
    list_display_links = ["ip_address", "user", "email"]
    list_filter = ["status"]
    search_fields = [
        "email",
        "ip_address",
        "device",
        "failure_reason",
    ]
    ready_only_fields = ["id", "timestamp"]
    ordering = ["-timestamp"]
