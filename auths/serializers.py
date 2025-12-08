import logging
from typing import Any, Dict, Optional

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from utils import choices
from utils import loggings
from .models import Passcode, Profile, User

# Initialize logger
logger = loggings.setup_logging()


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for User Registration.

    This serializer handles the creation of a new user. It validates the
    input data, ensures passwords match, and checks for password complexity.
    """

    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    confirm_password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "password",
            "confirm_password",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "username",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]

    def validate_email(self, value: str) -> str:
        """
        Normalize the email to lowercase and check for uniqueness.

        Args:
            value (str): The email provided by the user.

        Returns:
            str: The normalized email address.

        Raises:
            ValidationError: If the email already exists in the database.
        """
        email = value.lower().strip()
        if User.objects.filter(email=email).exists():
            logger.warning(f"Registration failed: Email already exists - {email}")
            raise serializers.ValidationError("A user with this email already exists.")
        return email

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate the data passed to the serializer.

        Checks:
        1. Passwords match.
        2. Password complexity.

        Args:
            attrs (Dict[str, Any]): The attributes to validate.

        Returns:
            Dict[str, Any]: The validated attributes.

        Raises:
            ValidationError: If passwords do not match or complexity fails.
        """
        logger.debug("Starting validation for user registration.")

        password = attrs.get("password")
        confirm_password = attrs.get("confirm_password")

        if password != confirm_password:
            logger.warning("Registration failed: Passwords do not match.")
            raise ValidationError({"password": "Passwords do not match."})

        try:
            if password:
                validate_password(password)
        except ValidationError as e:
            logger.warning(f"Registration failed: Password complexity error - {e}")
            raise serializers.ValidationError({"password": list(e.messages)})

        return attrs

    def create(self, validated_data: Dict[str, Any]) -> User:
        """
        Create a new user instance.

        Removes 'confirm_password' from the data and uses the UserManager
        to create the user safely.

        Args:
            validated_data (Dict[str, Any]): The validated data for user creation.

        Returns:
            User: The created user instance.

        Raises:
            ValidationError: If user creation fails.
        """
        email = validated_data.get("email")
        logger.info(f"Creating new user with email: {email}")

        validated_data.pop("confirm_password", None)

        try:
            user = User.objects.create_user(**validated_data)
            logger.info(f"User [{user.username}] created successfully")
            return user
        except Exception as e:
            logger.error(f"Error creating user {email}: {str(e)}")
            raise ValidationError(
                {"error": "Unable to create user. Please try again later."}
            )


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for the User model.

    This serializer is used to retrieve and update user information.
    It includes fields for user identification, profile details, and status.
    """

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "email",
            "is_active",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
        ]

    def update(self, instance: User, validated_data: Dict[str, Any]) -> User:
        """
        Update and return an existing `User` instance, given the validated data.

        Args:
            instance (User): The user instance to update.
            validated_data (Dict[str, Any]): The data to update.

        Returns:
            User: The updated user instance.
        """
        logger.info(f"Updating user [{instance.username}] info")
        return super().update(instance, validated_data)


class LoginSerializer(serializers.Serializer):
    """
    Serializer for User Login.

    Validates email and password, and authenticates the user.
    """

    email = serializers.EmailField(required=True, help_text="User's email address.")
    password = serializers.CharField(
        required=True, write_only=True, style={"input_type": "password"}
    )

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate credentials and authenticate user.

        Args:
            attrs (Dict[str, Any]): The attributes containing email and password.

        Returns:
            Dict[str, Any]: The validated attributes with the user object.

        Raises:
            ValidationError: If authentication fails.
        """
        email = attrs.get("email")
        password = attrs.get("password")

        if email and password:
            user = authenticate(
                request=self.context.get("request"), username=email, password=password
            )

            if not user:
                logger.warning(f"Login failed for email: {email} - Invalid credentials")
                msg = "Unable to log in with provided credentials."
                raise serializers.ValidationError(msg, code="authorization")
        else:
            msg = "Must include 'email' and 'password'."
            raise serializers.ValidationError(msg, code="authorization")

        attrs["user"] = user
        return attrs


class ProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for the Profile model.
    """

    class Meta:
        model = Profile
        fields = [
            "bio",
            "phone_number",
            "dob",
            "profile_picture",
            "gender",
            "occupation",
            "country",
            "city",
            "street",
            "zip_code",
        ]


class EmailVerificationSerializer(serializers.Serializer):
    """
    Serializer for Email Verification.

    This serializer handles the verification of a user's email address using
    a one-time passcode (OTP). It validates the email, OTP code, checks expiration,
    and ensures the code hasn't been used.
    """

    email = serializers.EmailField(
        required=True, help_text="Email address of the user to verify."
    )
    otp = serializers.CharField(
        required=True,
        min_length=8,
        max_length=8,
        help_text="8-digit one-time passcode sent to the user's email.",
    )

    def validate_email(self, value: str) -> str:
        """
        Normalize and validate the email address.

        Args:
            value (str): The email address to validate.

        Returns:
            str: Normalized email address (lowercase).

        Raises:
            ValidationError: If email format is invalid.
        """
        try:
            email = value.lower().strip()
            logger.debug(f"Validating email for verification: {email}")
            return email
        except Exception as e:
            logger.error(f"Error normalizing email: {str(e)}")
            raise serializers.ValidationError("Invalid email format.")

    def validate_otp(self, value: str) -> str:
        """
        Validate the OTP format.

        Args:
            value (str): The OTP code to validate.

        Returns:
            str: Cleaned OTP code.

        Raises:
            ValidationError: If OTP format is invalid.
        """
        otp = value.strip()

        # Ensure OTP is exactly 8 digits
        if not otp.isdigit():
            logger.warning("Invalid OTP format: contains non-digit characters")
            raise serializers.ValidationError("OTP must contain only digits.")

        if len(otp) != 8:
            logger.warning(f"Invalid OTP length: {len(otp)}")
            raise serializers.ValidationError("OTP must be exactly 8 digits.")

        return otp

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate the complete verification request using flat logic.

        Checks:
        1. User existence.
        2. Verification status.
        3. Passcode validity and expiration.

        Args:
            attrs (Dict[str, Any]): Dictionary containing email and otp.

        Returns:
            Dict[str, Any]: Validated attributes with user and passcode objects.
        """
        email = attrs.get("email")
        otp = attrs.get("otp")

        logger.info(f"Starting email validation for: {email}")

        # 1. Get User
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning(f"Verification attempt for non-existent email: {email}")
            raise serializers.ValidationError(
                {"email": "No account found with this email address."}
            )

        # 2. Check Verification Status
        if user.is_verified:
            logger.info(f"User {email} is already verified")
            raise serializers.ValidationError(
                {"email": "This account is already verified."}
            )

        # 3. Get Passcode
        try:
            passcode = Passcode.objects.get(
                user=user,
                code=otp,
                code_type=choices.CodeType.VERIFICATION,
                is_used=False,
            )
        except Passcode.DoesNotExist:
            logger.warning(f"Invalid OTP attempt for user {email}")
            raise serializers.ValidationError(
                {"otp": "Invalid verification code. Please check and try again."}
            )

        # 4. Check Expiration
        if passcode.expires_at < timezone.now():
            logger.warning(f"Expired OTP used for user {email}")
            passcode.is_used = True
            passcode.save(update_fields=["is_used"])
            raise serializers.ValidationError(
                {"otp": "This verification code has expired. Please request a new one."}
            )

        # Success - attach objects to attrs
        attrs["user"] = user
        attrs["passcode"] = passcode

        logger.info(f"Email verification validation successful for: {email}")
        return attrs


class UserListSerializer(serializers.ModelSerializer):
    """
    Serializer for listing users with their profile.
    """

    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "is_verified",
            "last_activity",
            "created_at",
            "updated_at",
            "profile",
        ]

    def get_profile(self, obj: User) -> Optional[Dict[str, Any]]:
        """
        Retrieve the user's profile if it exists.

        Args:
            obj (User): The user object.

        Returns:
            dict: Profile data or None.
        """
        if hasattr(obj, "user_profile"):
            return ProfileSerializer(obj.user_profile).data
        return None


class ResendOTPSerializer(serializers.Serializer):
    """
    Serializer for Resending OTP.

    This serializer handles requests to resend verification OTP to users
    who didn't receive it or whose OTP has expired.
    """

    email = serializers.EmailField(
        required=True, help_text="Email address of the user requesting OTP resend."
    )

    def validate_email(self, value: str) -> str:
        """
        Normalize and validate the email address.

        Args:
            value (str): The email address to validate.

        Returns:
            str: Normalized email address.

        Raises:
            ValidationError: If user doesn't exist or is already verified.
        """
        email = value.lower().strip()
        logger.debug(f"Validating email for OTP resend: {email}")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning(f"OTP resend attempt for non-existent email: {email}")
            raise serializers.ValidationError(
                "No account found with this email address."
            )

        if user.is_verified:
            logger.info(f"OTP resend attempt for already verified user: {email}")
            raise serializers.ValidationError(
                "This account is already verified. You can log in directly."
            )

        return email


class PasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for changing user password.

    Validates old password, ensures new password meets complexity requirements,
    and confirms new password matches confirmation.
    """

    old_password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        help_text="Current password for verification",
    )
    new_password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        help_text="New password. Must meet complexity requirements.",
    )
    confirm_new_password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        help_text="Confirm the new password",
    )

    def validate_old_password(self, value: str) -> str:
        """
        Validate that the old password is correct.
        """
        user = self.context.get("request").user

        if not user.check_password(value):
            logger.warning(f"Incorrect old password attempt for user: {user.email}")
            raise serializers.ValidationError("Current password is incorrect.")

        return value

    def validate_new_password(self, value: str) -> str:
        """
        Validate new password complexity.
        """
        try:
            validate_password(value)
            return value
        except ValidationError as e:
            logger.warning(f"Password complexity validation failed: {e}")
            raise serializers.ValidationError(list(e.messages))

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate that new passwords match and differ from old password.
        """
        old_password = attrs.get("old_password")
        new_password = attrs.get("new_password")
        confirm_new_password = attrs.get("confirm_new_password")

        if new_password != confirm_new_password:
            logger.warning("New password and confirmation do not match")
            raise serializers.ValidationError(
                {"confirm_new_password": "New passwords do not match."}
            )

        if old_password == new_password:
            logger.warning("New password is same as old password")
            raise serializers.ValidationError(
                {
                    "new_password": "New password must be different from current password."
                }
            )

        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    """
    Serializer for requesting password reset.

    Validates email and initiates password reset process by sending OTP.
    """

    email = serializers.EmailField(
        required=True, help_text="Email address of the account to reset password for"
    )

    def validate_email(self, value: str) -> str:
        """
        Validate and normalize email address.
        """
        email = value.lower().strip()

        # Check if user exists just for logging/internal logic,
        # but always return email to prevent enumeration in the View if desired.
        # However, typically serializers raise error if invalid data.
        # Here we follow the previous pattern of checking existence but passing safely.

        if not User.objects.filter(email=email).exists():
            logger.warning(f"Password reset requested for non-existent email: {email}")
            # We do NOT raise ValidationError here to prevent user enumeration
            # logic will be handled in view (if user is None, don't send email)
            pass

        return email


class PasswordResetVerifySerializer(serializers.Serializer):
    """
    Serializer for verifying password reset OTP.
    """

    email = serializers.EmailField(
        required=True, help_text="Email address of the account"
    )
    otp = serializers.CharField(
        required=True,
        min_length=8,
        max_length=8,
        help_text="8-digit OTP code sent to email",
    )

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate_otp(self, value: str) -> str:
        otp = value.strip()
        if not otp.isdigit() or len(otp) != 8:
            logger.warning(f"Invalid OTP format: {otp}")
            raise serializers.ValidationError("OTP must be exactly 8 digits.")
        return otp

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate OTP against database.
        """
        email = attrs.get("email")
        otp = attrs.get("otp")

        logger.info(f"Verifying password reset OTP for: {email}")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning(f"OTP verification for non-existent email: {email}")
            raise serializers.ValidationError(
                {"email": "No account found with this email address."}
            )

        try:
            passcode = Passcode.objects.get(
                user=user,
                code=otp,
                code_type=choices.CodeType.PASSWORD_RESET,
                is_used=False,
            )
        except Passcode.DoesNotExist:
            logger.warning(f"Invalid password reset OTP for {email}")
            raise serializers.ValidationError({"otp": "Invalid or expired reset code."})

        if passcode.expires_at < timezone.now():
            logger.warning(f"Expired password reset OTP for {email}")
            passcode.is_used = True
            passcode.save(update_fields=["is_used"])
            raise serializers.ValidationError(
                {"otp": "This reset code has expired. Please request a new one."}
            )

        attrs["user"] = user
        attrs["passcode"] = passcode
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    """
    Serializer for confirming password reset with new password.
    """

    email = serializers.EmailField(
        required=True, help_text="Email address of the account"
    )
    otp = serializers.CharField(
        required=True, min_length=8, max_length=8, help_text="8-digit OTP code"
    )
    new_password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        help_text="New password",
    )
    confirm_new_password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
        help_text="Confirm new password",
    )

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate_otp(self, value: str) -> str:
        otp = value.strip()
        if not otp.isdigit() or len(otp) != 8:
            raise serializers.ValidationError("Invalid OTP format.")
        return otp

    def validate_new_password(self, value: str) -> str:
        try:
            validate_password(value)
            return value
        except ValidationError as e:
            logger.warning("Password reset: weak password provided")
            raise serializers.ValidationError(list(e.messages))

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate OTP and password confirmation.
        """
        email = attrs.get("email")
        otp = attrs.get("otp")
        new_password = attrs.get("new_password")
        confirm_new_password = attrs.get("confirm_new_password")

        if new_password != confirm_new_password:
            logger.warning("Password reset: passwords don't match")
            raise serializers.ValidationError(
                {"confirm_new_password": "Passwords do not match."}
            )

        logger.info(f"Confirming password reset for: {email}")

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning(f"Password reset confirm for non-existent email: {email}")
            raise serializers.ValidationError(
                {"email": "No account found with this email address."}
            )

        try:
            passcode = Passcode.objects.get(
                user=user,
                code=otp,
                code_type=choices.CodeType.PASSWORD_RESET,
                is_used=False,
            )
        except Passcode.DoesNotExist:
            logger.warning(f"Invalid password reset OTP for {email}")
            raise serializers.ValidationError({"otp": "Invalid or expired reset code."})

        if passcode.expires_at < timezone.now():
            logger.warning(f"Expired password reset OTP for {email}")
            passcode.is_used = True
            passcode.save(update_fields=["is_used"])
            raise serializers.ValidationError(
                {"otp": "This reset code has expired. Please request a new one."}
            )

        attrs["user"] = user
        attrs["passcode"] = passcode
        logger.info(f"Password reset validation successful for: {email}")
        return attrs
