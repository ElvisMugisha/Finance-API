from datetime import timedelta
from typing import Any, Dict, Optional

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from utils import choices, loggings
from utils.throttlings import (
    check_brute_force,
    register_failed_login,
    reset_failed_logins,
)

from .models import Passcode, Profile, User, UserLoginAudit

# Initialize logger
logger = loggings.setup_logging()


class UserRegistrationSerializer(serializers.Serializer):
    """
    Serializer responsible for validating user input and creating a new user.

    This serializer intentionally avoids exposing internal User fields and
    delegates business rules to the model and manager.
    """

    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate_email(self, value: str) -> str:
        """Normalize and validate email uniqueness."""
        email = value.strip().lower()

        if User.objects.filter(email=email).exists():
            logger.info("Registration blocked: duplicate email", extra={"email": email})
            raise serializers.ValidationError("A user with this email already exists.")

        return email

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate password consistency and strength."""
        password = attrs.get("password")
        confirm_password = attrs.get("confirm_password")

        if password != confirm_password:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> User:
        """
        Create a new user using the custom UserManager.

        This method is intentionally minimal and relies on:
        - UserManager for invariants
        - Database constraints for consistency
        """
        validated_data.pop("confirm_password")

        try:
            user = User.objects.create_user(**validated_data)
        except Exception:
            logger.exception("User creation failed during registration")
            raise serializers.ValidationError(
                {"detail": "Unable to create user at this time."}
            )

        logger.info("User successfully registered", extra={"user_id": user.id})
        return user


class EmailVerificationSerializer(serializers.Serializer):
    """
    Validate email verification requests.

    Responsibilities:
    - Validate email and OTP format
    - Ensure user exists and is not already verified
    - Ensure OTP is valid, unused, and not expired

    NOTE:
    This serializer performs *validation only*.
    It does NOT mutate database state.
    """

    email = serializers.EmailField()
    otp = serializers.CharField(min_length=8, max_length=8)

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        email = attrs["email"].strip().lower()
        otp = attrs["otp"].strip()

        logger.info("Validating email verification request", extra={"email": email})

        # Validate OTP format
        if not otp.isdigit():
            raise serializers.ValidationError(
                {"otp": "Verification code must contain only digits."}
            )

        # Fetch user
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning(
                "Email verification failed: user not found", extra={"email": email}
            )
            raise serializers.ValidationError(
                {"email": "No account found with this email address."}
            )

        if user.is_verified:
            logger.info("Email already verified", extra={"email": email})
            raise serializers.ValidationError(
                {"email": "This email address is already verified."}
            )

        # Fetch active OTP
        try:
            passcode = Passcode.objects.get(
                user=user,
                code=otp,
                code_type=choices.CodeType.VERIFICATION,
                is_used=False,
            )
        except Passcode.DoesNotExist:
            logger.warning("Invalid verification OTP", extra={"email": email})
            raise serializers.ValidationError({"otp": "Invalid verification code."})

        # Expiration check
        if passcode.expires_at < timezone.now():
            logger.warning("Expired verification OTP used", extra={"email": email})
            raise serializers.ValidationError(
                {"otp": "This verification code has expired."}
            )

        attrs["user"] = user
        attrs["passcode"] = passcode
        return attrs


class ResendOTPSerializer(serializers.Serializer):
    """
    Validate resend OTP requests.

    Responsibilities:
    - Normalize email
    - Ensure user exists
    - Ensure user is not already verified

    NOTE:
    - No OTP or rate-limit logic here (SoC).
    """

    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        email = value.strip().lower()

        logger.debug("Validating resend OTP email", extra={"email": email})

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning("Resend OTP failed: user not found", extra={"email": email})
            raise serializers.ValidationError(
                "No account found with this email address."
            )

        if user.is_verified:
            logger.info(
                "Resend OTP blocked: account already verified",
                extra={"email": email},
            )
            raise serializers.ValidationError("This account is already verified.")

        self.context["user"] = user
        return email


class LoginSerializer(serializers.Serializer):
    """
    Authenticate user credentials and enforce account state rules.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs: dict) -> dict:
        email = attrs["email"].strip().lower()
        password = attrs["password"]
        request = self.context.get("request")

        logger.info(
            "Authenticating login attempt",
            extra={"email": email, "ip": request.META.get("REMOTE_ADDR")},
        )

        # Check brute-force lockouts
        if not check_brute_force(email):
            logger.warning(
                "Account temporarily locked due to failed attempts",
                extra={"email": email},
            )
            raise serializers.ValidationError(
                {
                    "detail": "Account temporarily locked due to too many failed login attempts."
                }
            )

        user = authenticate(request=request, username=email, password=password)
        if not user:
            logger.warning("Login failed: invalid credentials", extra={"email": email})
            register_failed_login(email)

            UserLoginAudit.log_event(
                user=None,
                email=email,
                status=choices.LoginStatus.FAILURE,
                ip_address=request.META.get("REMOTE_ADDR"),
                device=request.META.get("HTTP_USER_AGENT", ""),
                failure_reason="Invalid credentials",
            )

            raise serializers.ValidationError({"detail": "Invalid email or password."})

        # Successful login → reset failed attempts
        reset_failed_logins(email)

        if not user.is_active:
            logger.warning("Login blocked: inactive account", extra={"email": email})
            raise serializers.ValidationError({"detail": "This account is inactive."})

        if not user.is_verified:
            logger.warning("Login blocked: unverified account", extra={"email": email})
            raise serializers.ValidationError(
                {"detail": "Please verify your email before logging in."}
            )

        attrs["user"] = user
        return attrs


class PasswordChangeSerializer(serializers.Serializer):
    """
    Serializer responsible for validating password change requests.

    Responsibilities:
    - Verify the current (old) password
    - Enforce password complexity rules
    - Ensure new passwords match
    - Prevent password reuse

    This serializer performs validation ONLY.
    No database mutation occurs here.
    """

    old_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Current password for verification.",
    )
    new_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="New password. Must meet complexity requirements.",
    )
    confirm_new_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Confirmation of the new password.",
    )

    def validate_old_password(self, value: str) -> str:
        """
        Ensure the provided old password matches the user's current password.
        """
        user = self.context["request"].user

        if not user.check_password(value):
            logger.warning(
                "Password change failed: incorrect current password",
                extra={"user_id": user.id, "email": user.email},
            )
            raise serializers.ValidationError("Current password is incorrect.")

        return value

    def validate_new_password(self, value: str) -> str:
        """
        Validate password strength using Django's password validators.
        """
        try:
            validate_password(value)
        except ValidationError as exc:
            logger.warning(
                "Password complexity validation failed",
                extra={"errors": exc.messages},
            )
            raise serializers.ValidationError(exc.messages)

        return value

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cross-field validation:
        - New passwords must match
        - New password must differ from old password
        """
        old_password = attrs["old_password"]
        new_password = attrs["new_password"]
        confirm_password = attrs["confirm_new_password"]

        if new_password != confirm_password:
            logger.warning("Password change failed: password mismatch")
            raise serializers.ValidationError(
                {"confirm_new_password": "New passwords do not match."}
            )

        if old_password == new_password:
            logger.warning("Password change failed: password reuse attempt")
            raise serializers.ValidationError(
                {
                    "new_password": "New password must be different from the current password."
                }
            )

        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Serializer for initiating a password reset request.

    Responsibilities:
    - Normalize email
    - Validate format only (no existence checks to avoid enumeration)
    """

    email = serializers.EmailField(
        help_text="Email address associated with the account."
    )

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetVerifySerializer(serializers.Serializer):
    """
    Validates a password reset OTP.
    """

    email = serializers.EmailField()
    otp = serializers.CharField(min_length=8, max_length=8)

    def validate(self, attrs):
        email = attrs["email"].lower().strip()
        otp = attrs["otp"].strip()

        logger.info("Verifying password reset OTP", extra={"email": email})

        try:
            user = User.objects.get(email=email)
            passcode = Passcode.objects.get(
                user=user,
                code=otp,
                code_type=choices.CodeType.PASSWORD_RESET,
                is_used=False,
            )
        except (User.DoesNotExist, Passcode.DoesNotExist):
            logger.warning("Invalid password reset OTP attempt", extra={"email": email})
            raise serializers.ValidationError({"otp": "Invalid or expired reset code."})

        if passcode.expires_at < timezone.now():
            passcode.is_used = True
            passcode.save(update_fields=["is_used"])

            logger.warning("Expired password reset OTP used", extra={"email": email})
            raise serializers.ValidationError({"otp": "This reset code has expired."})

        attrs["user"] = user
        attrs["passcode"] = passcode
        return attrs


class PasswordResetSetNewSerializer(serializers.Serializer):
    """
    Sets a new password after OTP verification.
    """

    email = serializers.EmailField()
    otp = serializers.CharField(min_length=8, max_length=8)
    new_password = serializers.CharField(write_only=True)
    confirm_new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_new_password"]:
            raise serializers.ValidationError(
                {"confirm_new_password": "Passwords do not match."}
            )

        email = attrs["email"].lower().strip()
        otp = attrs["otp"].strip()

        try:
            user = User.objects.get(email=email)
            passcode = Passcode.objects.get(
                user=user,
                code=otp,
                code_type=choices.CodeType.PASSWORD_RESET,
                is_used=False,
            )
        except (User.DoesNotExist, Passcode.DoesNotExist):
            raise serializers.ValidationError({"otp": "Invalid or expired reset code."})

        if passcode.expires_at < timezone.now():
            passcode.is_used = True
            passcode.save(update_fields=["is_used"])
            raise serializers.ValidationError({"otp": "This reset code has expired."})

        attrs["user"] = user
        attrs["passcode"] = passcode
        return attrs


class ProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for managing user profile data.

    Responsibilities:
    - Validate API input
    - Enforce business rules at API boundary
    """

    annual_income = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        help_text="Annual income. Must be a non-negative decimal.",
    )

    currency_preference = serializers.CharField(
        max_length=3,
        required=False,
        help_text="ISO 4217 currency code (e.g. USD, RWF).",
    )

    class Meta:
        model = Profile
        fields = [
            "bio",
            "phone_number",
            "date_of_birth",
            "profile_picture",
            "gender",
            "occupation",
            "annual_income",
            "currency_preference",
            "notification_preferences",
            "privacy_settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_at", "updated_at")

    def validate_annual_income(self, value):
        """
        Ensure annual income is non-negative.
        """
        if value is not None and value < 0:
            raise serializers.ValidationError(
                "Annual income must be a non-negative amount."
            )
        return value

    def validate_date_of_birth(self, value):
        """
        Ensure date_of_birth is not in the future.
        """
        if value and value > timezone.now().date():
            raise serializers.ValidationError("Date of birth cannot be in the future.")
        return value


class UserListSerializer(serializers.ModelSerializer):
    """
    Serializer for listing users with optional profile data.

    Responsibilities:
    - Serialize user fields
    - Include profile data if it exists
    """

    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "is_premium",
            "premium_expires",
            "is_active",
            "is_verified",
            "is_staff",
            "is_superuser",
            "last_login",
            "last_activity_at",
            "created_at",
            "updated_at",
            "profile",
        ]

    def get_profile(self, obj: User) -> Optional[Dict[str, Any]]:
        """
        Safely return serialized profile data if available.

        Args:
            obj (User): User instance.

        Returns:
            dict | None: Serialized profile or None if absent.
        """
        profile = getattr(obj, "profile", None)
        if not profile:
            return None

        return ProfileSerializer(profile).data


class UserMeSerializer(serializers.ModelSerializer):
    """
    Serializer for the authenticated user's full profile.

    Includes nested profile data if it exists.
    """

    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "is_premium",
            "premium_expires",
            "last_login",
            "last_activity_at",
            "created_at",
            "updated_at",
            "profile",
        ]
