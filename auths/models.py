import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin

from .manager import UserManager
from utils import loggings, choices

# Initialize logger
logger = loggings.setup_logging()


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model extending AbstractBaseUser and PermissionsMixin.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, unique=True
    )
    username = models.CharField(max_length=255, unique=True, null=False)
    first_name = models.CharField(max_length=255, null=False, blank=False)
    middle_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(max_length=255, null=False, blank=False)
    email = models.EmailField(max_length=255, unique=True)

    is_superuser = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)

    last_activity = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Field to use for authentication (usually 'email' or 'username', email in this case)
    USERNAME_FIELD = "email"

    # Required fields when creating a user
    REQUIRED_FIELDS = [
        "first_name",
        "last_name",
    ]

    # Custom UserManager class to handle user creation and management
    objects = UserManager()

    def __str__(self):
        """
        String representation of the user model, which returns the user's email.
        """
        return self.email

    @property
    def full_name(self):
        """
        Return the full name of the user, combining first_name and last_name.
        """
        return f"{self.first_name} {self.last_name}".strip()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_at"]
        db_table = "users"


class Passcode(models.Model):
    """
    Store passcodes for user verification or password reset.
    Supports multiple OTPs per user with a code_type to distinguish purposes.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, unique=True
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="user_passcodes",
        null=False,
        blank=False,
    )
    code = models.CharField(max_length=15, unique=True, null=False, blank=False)
    code_type = models.CharField(
        max_length=20,
        choices=choices.CodeType.choices,
        null=False,
        blank=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=False, blank=False)
    is_used = models.BooleanField(default=False)

    def __str__(self):
        """
        String representation of the passcode.
        """
        return f"{self.user.username} | {self.code_type}"

    class Meta:
        verbose_name = "Passcode"
        verbose_name_plural = "Passcodes"
        ordering = ["-created_at"]
        db_table = "passcodes"

        # Ensure only one active passcode per user per code_type
        constraints = [
            models.UniqueConstraint(
                fields=["user", "code_type"],
                condition=models.Q(is_used=False),
                name="unique_active_passcode_per_user_per_type",
            )
        ]


class Profile(models.Model):
    """
    Representing user's profile with additional personal information.
    """

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, unique=True
    )
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="user_profile",
        null=False,
        blank=False,
    )
    bio = models.TextField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    dob = models.DateField(null=True, blank=True)
    profile_picture = models.ImageField(upload_to="profiles/", null=True, blank=True)
    gender = models.CharField(
        max_length=20,
        choices=choices.Gender.choices,
        null=True,
        blank=True,
    )

    occupation = models.CharField(max_length=255, null=True, blank=True)

    country = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=255, null=True, blank=True)
    street = models.CharField(max_length=255, null=True, blank=True)
    zip_code = models.CharField(max_length=20, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """
        String representation of the user profile.
        """
        return f"{self.user.first_name}'s Profile"

    class Meta:
        verbose_name = "Profile"
        verbose_name_plural = "Profiles"
        ordering = ["-created_at"]
        db_table = "profiles"

        # Ensure only one profile per user
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                name="unique_profile_per_user",
            )
        ]
