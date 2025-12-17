import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone
from django.core.exceptions import ValidationError

from utils import choices, loggings
from .manager import UserManager

logger = loggings.setup_logging()


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model using email as the primary authentication identifier.

    Design principles:
    - Email is the login credential (USERNAME_FIELD)
    - UUID primary key for distributed safety
    - Explicit lifecycle fields
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    email = models.EmailField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Primary identifier used for authentication",
    )
    username = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Public-facing identifier (non-auth)",
    )

    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True, null=True)
    last_name = models.CharField(max_length=150)

    is_premium = models.BooleanField(default=False)
    premium_expires = models.DateTimeField(blank=True, null=True)

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)

    last_login = models.DateTimeField(blank=True, null=True)
    last_activity_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"

    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_at"]
        db_table = "users"
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["username"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self):
        """Enforce business invariants."""
        if self.is_premium and not self.premium_expires:
            raise ValidationError("Premium users must have a premium expiration date.")

    def get_full_name(self) -> str:
        """Return the user's full name."""
        return " ".join(
            filter(None, [self.first_name, self.middle_name, self.last_name])
        )

    def get_short_name(self) -> str:
        """Return a short display name."""
        return self.first_name

    @property
    def full_name(self) -> str:
        """Property alias for get_full_name."""
        return self.get_full_name()

    @property
    def is_premium_active(self) -> bool:
        """Check if premium access is currently active."""
        return (
            self.is_premium
            and self.premium_expires
            and self.premium_expires >= timezone.now()
        )


class Passcode(models.Model):
    """
    Represents a single-use, time-bound passcode (OTP).

    Supports:
    - Verification
    - Password reset
    - Other short-lived authentication flows
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="passcodes",
    )

    code = models.CharField(
        max_length=15,
        help_text="One-time passcode value",
    )
    code_type = models.CharField(
        max_length=20,
        choices=choices.CodeType.choices,
    )

    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Passcode"
        verbose_name_plural = "Passcodes"
        db_table = "passcodes"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code_type"],
                condition=models.Q(is_used=False),
                name="unique_active_passcode_per_user_and_type",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="expires_after_creation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.email} | {self.code_type}"

    def is_expired(self) -> bool:
        """Check if the passcode is expired."""
        return timezone.now() >= self.expires_at

    def mark_used(self) -> None:
        """Mark the passcode as used."""
        if self.is_used:
            logger.warning("Attempted to reuse a passcode", extra={"id": self.id})
            return
        self.is_used = True
        self.save(update_fields=["is_used"])


class Profile(models.Model):
    """
    User profile storing non-auth, non-security personal data.

    Intentionally separated from User for:
    - Security isolation
    - Optional loading
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    bio = models.TextField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)

    profile_picture = models.ImageField(
        upload_to="profiles/%Y/%m/%d/",
        blank=True,
        null=True,
    )

    gender = models.CharField(
        max_length=20,
        choices=choices.Gender.choices,
        blank=True,
        null=True,
    )

    occupation = models.CharField(max_length=255, blank=True, null=True)
    annual_income = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True
    )

    currency_preference = models.CharField(
        max_length=3, default="USD", null=True, blank=True
    )

    notification_preferences = models.JSONField(default=dict, null=True, blank=True)
    privacy_settings = models.JSONField(default=dict, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"
        ordering = ["-created_at"]
        db_table = "profiles"

    def __str__(self) -> str:
        return f"Profile({self.user.email})"
