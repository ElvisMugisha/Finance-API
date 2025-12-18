from typing import Any, Dict, Optional
from datetime import timedelta
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
    Validate OTP resend requests.

    Responsibilities:
    - Ensure user exists
    - Ensure user is not already verified
    - Detect active (unexpired, unused) OTPs
    - Provide remaining TTL if OTP is still valid

    NOTE:
    This serializer performs validation ONLY.
    No database mutation occurs here.
    """

    email = serializers.EmailField()

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        email = attrs["email"].strip().lower()
        logger.info("Validating OTP resend request", extra={"email": email})

        # Fetch user
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning("OTP resend failed: user not found", extra={"email": email})
            raise serializers.ValidationError(
                {"email": "No account found with this email address."}
            )

        if user.is_verified:
            logger.info("OTP resend blocked: already verified", extra={"email": email})
            raise serializers.ValidationError(
                {"email": "This account is already verified."}
            )

        # Check for active OTP
        active_otp = (
            Passcode.objects.filter(
                user=user,
                code_type=choices.CodeType.VERIFICATION,
                is_used=False,
                expires_at__gt=timezone.now(),
            )
            .order_by("-expires_at")
            .first()
        )

        if active_otp:
            remaining_seconds = int(
                (active_otp.expires_at - timezone.now()).total_seconds()
            )

            logger.warning(
                "Active OTP still valid",
                extra={
                    "email": email,
                    "remaining_seconds": remaining_seconds,
                },
            )

            raise serializers.ValidationError(
                {
                    "otp": (
                        "An active verification code already exists. "
                        f"Please wait {remaining_seconds} seconds before requesting a new one."
                    )
                }
            )

        attrs["user"] = user
        return attrs


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


# class UserSerializer(serializers.ModelSerializer):
#     """
#     Serializer for the User model.

#     This serializer is used to retrieve and update user information.
#     It includes fields for user identification, profile details, and status.
#     """

#     class Meta:
#         model = User
#         fields = [
#             "id",
#             "username",
#             "email",
#             "first_name",
#             "last_name",
#             "is_premium",
#             "premium_expires",
#             "is_active",
#             "is_verified",
#             "last_activity",
#             "created_at",
#             "updated_at",
#         ]
#         read_only_fields = [
#             "id",
#             "email",
#             "is_premium",
#             "premium_expires",
#             "is_active",
#             "is_verified",
#             "last_activity",
#             "created_at",
#             "updated_at",
#         ]

#     def update(self, instance: User, validated_data: Dict[str, Any]) -> User:
#         """
#         Update and return an existing `User` instance, given the validated data.

#         Args:
#             instance (User): The user instance to update.
#             validated_data (Dict[str, Any]): The data to update.

#         Returns:
#             User: The updated user instance.
#         """
#         logger.info(f"Updating user [{instance.username}] info")
#         return super().update(instance, validated_data)


# class ProfileSerializer(serializers.ModelSerializer):
#     """
#     Serializer for the Profile model.
#     """

#     class Meta:
#         model = Profile
#         fields = [
#             "bio",
#             "phone_number",
#             "dob",
#             "profile_picture",
#             "gender",
#             "occupation",
#             "annual_income",
#             "monthly_income_target",
#             "emergency_fund_target",
#             "currency_preference",
#             "financial_goals",
#             "risk_tolerance",
#             "financial_advisor",
#             "retirement_goal",
#             "investment_experience",
#             "notification_preferences",
#             "privacy_settings",
#             "country",
#             "city",
#             "street",
#             "zip_code",
#         ]


# class UserListSerializer(serializers.ModelSerializer):
#     """
#     Serializer for listing users with their profile.
#     """

#     profile = serializers.SerializerMethodField()

#     class Meta:
#         model = User
#         fields = [
#             "id",
#             "email",
#             "username",
#             "first_name",
#             "middle_name",
#             "last_name",
#             "is_active",
#             "is_verified",
#             "is_staff",
#             "is_superuser",
#             "last_activity",
#             "created_at",
#             "updated_at",
#             "profile",
#         ]

#     def get_profile(self, obj: User) -> Optional[Dict[str, Any]]:
#         """
#         Retrieve the user's profile if it exists.

#         Args:
#             obj (User): The user object.

#         Returns:
#             dict: Profile data or None.
#         """
#         if hasattr(obj, "user_profile"):
#             return ProfileSerializer(obj.user_profile).data
#         return None


# class PasswordChangeSerializer(serializers.Serializer):
#     """
#     Serializer for changing user password.

#     Validates old password, ensures new password meets complexity requirements,
#     and confirms new password matches confirmation.
#     """

#     old_password = serializers.CharField(
#         required=True,
#         write_only=True,
#         style={"input_type": "password"},
#         help_text="Current password for verification",
#     )
#     new_password = serializers.CharField(
#         required=True,
#         write_only=True,
#         style={"input_type": "password"},
#         help_text="New password. Must meet complexity requirements.",
#     )
#     confirm_new_password = serializers.CharField(
#         required=True,
#         write_only=True,
#         style={"input_type": "password"},
#         help_text="Confirm the new password",
#     )

#     def validate_old_password(self, value: str) -> str:
#         """
#         Validate that the old password is correct.
#         """
#         user = self.context.get("request").user

#         if not user.check_password(value):
#             logger.warning(f"Incorrect old password attempt for user: {user.email}")
#             raise serializers.ValidationError("Current password is incorrect.")

#         return value

#     def validate_new_password(self, value: str) -> str:
#         """
#         Validate new password complexity.
#         """
#         try:
#             validate_password(value)
#             return value
#         except ValidationError as e:
#             logger.warning(f"Password complexity validation failed: {e}")
#             raise serializers.ValidationError(list(e.messages))

#     def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
#         """
#         Validate that new passwords match and differ from old password.
#         """
#         old_password = attrs.get("old_password")
#         new_password = attrs.get("new_password")
#         confirm_new_password = attrs.get("confirm_new_password")

#         if new_password != confirm_new_password:
#             logger.warning("New password and confirmation do not match")
#             raise serializers.ValidationError(
#                 {"confirm_new_password": "New passwords do not match."}
#             )

#         if old_password == new_password:
#             logger.warning("New password is same as old password")
#             raise serializers.ValidationError(
#                 {
#                     "new_password": "New password must be different from current password."
#                 }
#             )

#         return attrs


# class PasswordResetRequestSerializer(serializers.Serializer):
#     """
#     Serializer for requesting password reset.

#     Validates email and initiates password reset process by sending OTP.
#     """

#     email = serializers.EmailField(
#         required=True, help_text="Email address of the account to reset password for"
#     )

#     def validate_email(self, value: str) -> str:
#         """
#         Validate and normalize email address.
#         """
#         email = value.lower().strip()

#         # Check if user exists just for logging/internal logic,
#         # but always return email to prevent enumeration in the View if desired.
#         # However, typically serializers raise error if invalid data.
#         # Here we follow the previous pattern of checking existence but passing safely.

#         if not User.objects.filter(email=email).exists():
#             logger.warning(f"Password reset requested for non-existent email: {email}")
#             # We do NOT raise ValidationError here to prevent user enumeration
#             # logic will be handled in view (if user is None, don't send email)
#             pass

#         return email


# class PasswordResetVerifySerializer(serializers.Serializer):
#     """
#     Serializer for verifying password reset OTP.
#     """

#     email = serializers.EmailField(
#         required=True, help_text="Email address of the account"
#     )
#     otp = serializers.CharField(
#         required=True,
#         min_length=8,
#         max_length=8,
#         help_text="8-digit OTP code sent to email",
#     )

#     def validate_email(self, value: str) -> str:
#         return value.lower().strip()

#     def validate_otp(self, value: str) -> str:
#         otp = value.strip()
#         if not otp.isdigit() or len(otp) != 8:
#             logger.warning(f"Invalid OTP format: {otp}")
#             raise serializers.ValidationError("OTP must be exactly 8 digits.")
#         return otp

#     def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
#         """
#         Validate OTP against database.
#         """
#         email = attrs.get("email")
#         otp = attrs.get("otp")

#         logger.info(f"Verifying password reset OTP for: {email}")

#         try:
#             user = User.objects.get(email=email)
#         except User.DoesNotExist:
#             logger.warning(f"OTP verification for non-existent email: {email}")
#             raise serializers.ValidationError(
#                 {"email": "No account found with this email address."}
#             )

#         try:
#             passcode = Passcode.objects.get(
#                 user=user,
#                 code=otp,
#                 code_type=choices.CodeType.PASSWORD_RESET,
#                 is_used=False,
#             )
#         except Passcode.DoesNotExist:
#             logger.warning(f"Invalid password reset OTP for {email}")
#             raise serializers.ValidationError({"otp": "Invalid or expired reset code."})

#         if passcode.expires_at < timezone.now():
#             logger.warning(f"Expired password reset OTP for {email}")
#             passcode.is_used = True
#             passcode.save(update_fields=["is_used"])
#             raise serializers.ValidationError(
#                 {"otp": "This reset code has expired. Please request a new one."}
#             )

#         attrs["user"] = user
#         attrs["passcode"] = passcode
#         return attrs


# class PasswordResetConfirmSerializer(serializers.Serializer):
#     """
#     Serializer for confirming password reset with new password.
#     """

#     email = serializers.EmailField(
#         required=True, help_text="Email address of the account"
#     )
#     otp = serializers.CharField(
#         required=True, min_length=8, max_length=8, help_text="8-digit OTP code"
#     )
#     new_password = serializers.CharField(
#         required=True,
#         write_only=True,
#         style={"input_type": "password"},
#         help_text="New password",
#     )
#     confirm_new_password = serializers.CharField(
#         required=True,
#         write_only=True,
#         style={"input_type": "password"},
#         help_text="Confirm new password",
#     )

#     def validate_email(self, value: str) -> str:
#         return value.lower().strip()

#     def validate_otp(self, value: str) -> str:
#         otp = value.strip()
#         if not otp.isdigit() or len(otp) != 8:
#             raise serializers.ValidationError("Invalid OTP format.")
#         return otp

#     def validate_new_password(self, value: str) -> str:
#         try:
#             validate_password(value)
#             return value
#         except ValidationError as e:
#             logger.warning("Password reset: weak password provided")
#             raise serializers.ValidationError(list(e.messages))

#     def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
#         """
#         Validate OTP and password confirmation.
#         """
#         email = attrs.get("email")
#         otp = attrs.get("otp")
#         new_password = attrs.get("new_password")
#         confirm_new_password = attrs.get("confirm_new_password")

#         if new_password != confirm_new_password:
#             logger.warning("Password reset: passwords don't match")
#             raise serializers.ValidationError(
#                 {"confirm_new_password": "Passwords do not match."}
#             )

#         logger.info(f"Confirming password reset for: {email}")

#         try:
#             user = User.objects.get(email=email)
#         except User.DoesNotExist:
#             logger.warning(f"Password reset confirm for non-existent email: {email}")
#             raise serializers.ValidationError(
#                 {"email": "No account found with this email address."}
#             )

#         try:
#             passcode = Passcode.objects.get(
#                 user=user,
#                 code=otp,
#                 code_type=choices.CodeType.PASSWORD_RESET,
#                 is_used=False,
#             )
#         except Passcode.DoesNotExist:
#             logger.warning(f"Invalid password reset OTP for {email}")
#             raise serializers.ValidationError({"otp": "Invalid or expired reset code."})

#         if passcode.expires_at < timezone.now():
#             logger.warning(f"Expired password reset OTP for {email}")
#             passcode.is_used = True
#             passcode.save(update_fields=["is_used"])
#             raise serializers.ValidationError(
#                 {"otp": "This reset code has expired. Please request a new one."}
#             )

#         attrs["user"] = user
#         attrs["passcode"] = passcode
#         logger.info(f"Password reset validation successful for: {email}")
#         return attrs
