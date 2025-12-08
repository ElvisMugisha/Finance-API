"""
Comprehensive pytest tests for auths serializers.

This module contains unit tests for all serializers in the auths app,
ensuring proper validation, error handling, and data processing.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from auths.models import Passcode, Profile
from auths.serializers import (
    EmailVerificationSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
    ProfileSerializer,
    ResendOTPSerializer,
    UserListSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)
from utils import choices

User = get_user_model()


@pytest.mark.django_db
class TestUserRegistrationSerializer:
    """Test suite for UserRegistrationSerializer."""

    def test_valid_registration(self, user_data):
        """Test successful user registration with valid data."""
        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid()
        user = serializer.save()

        assert user.email == "test@example.com"
        assert user.first_name == "John"
        assert user.last_name == "Doe"
        assert user.check_password("SecurePass123!")
        assert not user.is_verified

    def test_email_normalization(self, user_data):
        """Test that email is normalized to lowercase."""
        user_data["email"] = "TEST@EXAMPLE.COM"

        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid()
        user = serializer.save()

        assert user.email == "test@example.com"

    def test_duplicate_email(self, create_user, user_data):
        """Test that duplicate email raises validation error."""
        create_user(email="test@example.com")

        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_password_mismatch(self, user_data):
        """Test that mismatched passwords raise validation error."""
        user_data["confirm_password"] = "DifferentPass123!"

        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        assert "password" in serializer.errors

    def test_weak_password(self, user_data):
        """Test that weak password raises validation error."""
        user_data["password"] = "weak"
        user_data["confirm_password"] = "weak"

        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        # Password errors can be in 'password' or 'non_field_errors'
        assert (
            "password" in serializer.errors or "non_field_errors" in serializer.errors
        )

    def test_missing_required_fields(self):
        """Test that missing required fields raise validation errors."""
        serializer = UserRegistrationSerializer(data={})
        assert not serializer.is_valid()

        assert "email" in serializer.errors
        assert "first_name" in serializer.errors
        assert "last_name" in serializer.errors
        assert "password" in serializer.errors

    def test_confirm_password_not_in_created_user(self, user_data):
        """Test that confirm_password is not saved to the database."""
        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid()
        user = serializer.save()

        assert not hasattr(user, "confirm_password")


@pytest.mark.django_db
class TestUserSerializer:
    """Test suite for UserSerializer."""

    def test_serialization(self, create_user):
        """Test user serialization."""
        user = create_user()
        serializer = UserSerializer(user)
        data = serializer.data

        assert data["email"] == "test@example.com"
        assert data["first_name"] == "John"
        assert data["last_name"] == "Doe"
        assert "id" in data
        assert "username" in data

    def test_update_user(self, create_user):
        """Test updating user information."""
        user = create_user()
        serializer = UserSerializer(
            user, data={"first_name": "Jane", "last_name": "Smith"}, partial=True
        )
        assert serializer.is_valid()
        updated_user = serializer.save()

        assert updated_user.first_name == "Jane"
        assert updated_user.last_name == "Smith"

    def test_read_only_fields(self, create_user):
        """Test that read-only fields cannot be updated."""
        user = create_user()
        original_email = user.email

        serializer = UserSerializer(
            user, data={"email": "newemail@example.com"}, partial=True
        )
        assert serializer.is_valid()
        updated_user = serializer.save()

        # Email should not change
        assert updated_user.email == original_email


@pytest.mark.django_db
class TestLoginSerializer:
    """Test suite for LoginSerializer."""

    @pytest.fixture
    def request_factory(self):
        """Provide a request factory."""
        return RequestFactory()

    def test_valid_login(self, create_user, request_factory):
        """Test successful login with valid credentials."""
        user = create_user()
        request = request_factory.post("/login/")
        data = {"email": "test@example.com", "password": "SecurePass123!"}

        serializer = LoginSerializer(data=data, context={"request": request})
        assert serializer.is_valid()
        assert serializer.validated_data["user"] == user

    def test_invalid_password(self, create_user, request_factory):
        """Test login with invalid password."""
        create_user()
        request = request_factory.post("/login/")
        data = {"email": "test@example.com", "password": "WrongPassword!"}

        serializer = LoginSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()

    def test_invalid_email(self, request_factory):
        """Test login with non-existent email."""
        request = request_factory.post("/login/")
        data = {"email": "nonexistent@example.com", "password": "SecurePass123!"}

        serializer = LoginSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()

    def test_missing_credentials(self, request_factory):
        """Test login with missing credentials."""
        request = request_factory.post("/login/")

        # Missing password
        serializer = LoginSerializer(
            data={"email": "test@example.com"}, context={"request": request}
        )
        assert not serializer.is_valid()

        # Missing email
        serializer = LoginSerializer(
            data={"password": "Pass123!"}, context={"request": request}
        )
        assert not serializer.is_valid()


@pytest.mark.django_db
class TestProfileSerializer:
    """Test suite for ProfileSerializer."""

    @pytest.fixture
    def profile(self, create_user):
        """Create a profile for testing."""
        user = create_user()
        return Profile.objects.create(
            user=user,
            bio="Test bio",
            phone_number="+1234567890",
            gender=choices.Gender.MALE,
            country="USA",
            city="New York",
        )

    def test_profile_serialization(self, profile):
        """Test profile serialization."""
        serializer = ProfileSerializer(profile)
        data = serializer.data

        assert data["bio"] == "Test bio"
        assert data["phone_number"] == "+1234567890"
        assert data["gender"] == choices.Gender.MALE
        assert data["country"] == "USA"
        assert data["city"] == "New York"

    def test_profile_update(self, profile):
        """Test updating profile."""
        serializer = ProfileSerializer(
            profile, data={"bio": "Updated bio", "city": "Los Angeles"}, partial=True
        )
        assert serializer.is_valid()
        updated_profile = serializer.save()

        assert updated_profile.bio == "Updated bio"
        assert updated_profile.city == "Los Angeles"


@pytest.mark.django_db
class TestEmailVerificationSerializer:
    """Test suite for EmailVerificationSerializer."""

    @pytest.fixture
    def user_with_passcode(self, create_user):
        """Create a user with a verification passcode."""
        user = create_user()
        passcode = Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.VERIFICATION,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return user, passcode

    def test_valid_verification(self, user_with_passcode):
        """Test successful email verification."""
        user, passcode = user_with_passcode
        data = {"email": "test@example.com", "otp": "12345678"}

        serializer = EmailVerificationSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["user"] == user
        assert serializer.validated_data["passcode"] == passcode

    def test_invalid_otp(self, user_with_passcode):
        """Test verification with invalid OTP."""
        data = {"email": "test@example.com", "otp": "87654321"}

        serializer = EmailVerificationSerializer(data=data)
        assert not serializer.is_valid()
        assert "otp" in serializer.errors

    def test_expired_otp(self, user_with_passcode):
        """Test verification with expired OTP."""
        user, passcode = user_with_passcode
        passcode.expires_at = timezone.now() - timedelta(minutes=10)
        passcode.save()

        data = {"email": "test@example.com", "otp": "12345678"}

        serializer = EmailVerificationSerializer(data=data)
        assert not serializer.is_valid()
        assert "otp" in serializer.errors

    def test_already_verified_user(self, user_with_passcode):
        """Test verification for already verified user."""
        user, _ = user_with_passcode
        user.is_verified = True
        user.save()

        data = {"email": "test@example.com", "otp": "12345678"}

        serializer = EmailVerificationSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_nonexistent_user(self):
        """Test verification for non-existent user."""
        data = {"email": "nonexistent@example.com", "otp": "12345678"}

        serializer = EmailVerificationSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_otp_format_validation(self, user_with_passcode):
        """Test OTP format validation."""
        # Non-digit OTP
        serializer = EmailVerificationSerializer(
            data={"email": "test@example.com", "otp": "abcd1234"}
        )
        assert not serializer.is_valid()

        # Wrong length OTP
        serializer = EmailVerificationSerializer(
            data={"email": "test@example.com", "otp": "123456"}
        )
        assert not serializer.is_valid()

    def test_used_otp(self, user_with_passcode):
        """Test verification with already used OTP."""
        user, passcode = user_with_passcode
        passcode.is_used = True
        passcode.save()

        data = {"email": "test@example.com", "otp": "12345678"}

        serializer = EmailVerificationSerializer(data=data)
        assert not serializer.is_valid()


@pytest.mark.django_db
class TestResendOTPSerializer:
    """Test suite for ResendOTPSerializer."""

    def test_valid_resend_request(self, create_user):
        """Test valid OTP resend request."""
        create_user()
        data = {"email": "test@example.com"}

        serializer = ResendOTPSerializer(data=data)
        assert serializer.is_valid()

    def test_already_verified_user(self, create_user):
        """Test resend request for already verified user."""
        user = create_user()
        user.is_verified = True
        user.save()

        data = {"email": "test@example.com"}

        serializer = ResendOTPSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_nonexistent_user(self):
        """Test resend request for non-existent user."""
        data = {"email": "nonexistent@example.com"}

        serializer = ResendOTPSerializer(data=data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_email_normalization(self, create_user):
        """Test that email is normalized."""
        create_user()
        data = {"email": "TEST@EXAMPLE.COM"}

        serializer = ResendOTPSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["email"] == "test@example.com"


@pytest.mark.django_db
class TestPasswordChangeSerializer:
    """Test suite for PasswordChangeSerializer."""

    @pytest.fixture
    def user_and_request(self, create_user):
        """Create a user and mock request."""
        user = create_user(password="OldPass123!")
        factory = RequestFactory()
        request = factory.post("/change-password/")
        request.user = user
        return user, request

    def test_valid_password_change(self, user_and_request):
        """Test successful password change."""
        _, request = user_and_request
        data = {
            "old_password": "OldPass123!",
            "new_password": "NewPass123!",
            "confirm_new_password": "NewPass123!",
        }

        serializer = PasswordChangeSerializer(data=data, context={"request": request})
        assert serializer.is_valid()

    def test_incorrect_old_password(self, user_and_request):
        """Test password change with incorrect old password."""
        _, request = user_and_request
        data = {
            "old_password": "WrongPass123!",
            "new_password": "NewPass123!",
            "confirm_new_password": "NewPass123!",
        }

        serializer = PasswordChangeSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "old_password" in serializer.errors

    def test_password_mismatch(self, user_and_request):
        """Test password change with mismatched new passwords."""
        _, request = user_and_request
        data = {
            "old_password": "OldPass123!",
            "new_password": "NewPass123!",
            "confirm_new_password": "DifferentPass123!",
        }

        serializer = PasswordChangeSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "confirm_new_password" in serializer.errors

    def test_same_old_and_new_password(self, user_and_request):
        """Test password change with same old and new password."""
        _, request = user_and_request
        data = {
            "old_password": "OldPass123!",
            "new_password": "OldPass123!",
            "confirm_new_password": "OldPass123!",
        }

        serializer = PasswordChangeSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        assert "new_password" in serializer.errors

    def test_weak_new_password(self, user_and_request):
        """Test password change with weak new password."""
        _, request = user_and_request
        data = {
            "old_password": "OldPass123!",
            "new_password": "weak",
            "confirm_new_password": "weak",
        }

        serializer = PasswordChangeSerializer(data=data, context={"request": request})
        assert not serializer.is_valid()
        # Password errors are in 'new_password' field
        assert (
            "new_password" in serializer.errors
            or "non_field_errors" in serializer.errors
        )


@pytest.mark.django_db
class TestPasswordResetRequestSerializer:
    """Test suite for PasswordResetRequestSerializer."""

    def test_valid_reset_request(self, create_user):
        """Test valid password reset request."""
        create_user()
        data = {"email": "test@example.com"}

        serializer = PasswordResetRequestSerializer(data=data)
        assert serializer.is_valid()

    def test_email_normalization(self, create_user):
        """Test that email is normalized."""
        create_user()
        data = {"email": "TEST@EXAMPLE.COM"}

        serializer = PasswordResetRequestSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["email"] == "test@example.com"

    def test_nonexistent_email(self):
        """Test reset request for non-existent email (should still validate)."""
        # For security, we don't reveal if email exists
        data = {"email": "nonexistent@example.com"}

        serializer = PasswordResetRequestSerializer(data=data)
        assert serializer.is_valid()


@pytest.mark.django_db
class TestPasswordResetVerifySerializer:
    """Test suite for PasswordResetVerifySerializer."""

    @pytest.fixture
    def user_with_reset_code(self, create_user):
        """Create a user with a password reset passcode."""
        user = create_user()
        passcode = Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return user, passcode

    def test_valid_otp_verification(self, user_with_reset_code):
        """Test successful OTP verification."""
        user, _ = user_with_reset_code
        data = {"email": "test@example.com", "otp": "12345678"}

        serializer = PasswordResetVerifySerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["user"] == user

    def test_invalid_otp(self, user_with_reset_code):
        """Test verification with invalid OTP."""
        data = {"email": "test@example.com", "otp": "87654321"}

        serializer = PasswordResetVerifySerializer(data=data)
        assert not serializer.is_valid()
        assert "otp" in serializer.errors

    def test_expired_otp(self, user_with_reset_code):
        """Test verification with expired OTP."""
        _, passcode = user_with_reset_code
        passcode.expires_at = timezone.now() - timedelta(minutes=10)
        passcode.save()

        data = {"email": "test@example.com", "otp": "12345678"}

        serializer = PasswordResetVerifySerializer(data=data)
        assert not serializer.is_valid()
        assert "otp" in serializer.errors


@pytest.mark.django_db
class TestPasswordResetConfirmSerializer:
    """Test suite for PasswordResetConfirmSerializer."""

    @pytest.fixture
    def user_with_reset_code(self, create_user):
        """Create a user with a password reset passcode."""
        user = create_user(password="OldPass123!")
        passcode = Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return user, passcode

    def test_valid_password_reset(self, user_with_reset_code):
        """Test successful password reset confirmation."""
        user, _ = user_with_reset_code
        data = {
            "email": "test@example.com",
            "otp": "12345678",
            "new_password": "NewPass123!",
            "confirm_new_password": "NewPass123!",
        }

        serializer = PasswordResetConfirmSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["user"] == user

    def test_password_mismatch(self, user_with_reset_code):
        """Test password reset with mismatched passwords."""
        data = {
            "email": "test@example.com",
            "otp": "12345678",
            "new_password": "NewPass123!",
            "confirm_new_password": "DifferentPass123!",
        }

        serializer = PasswordResetConfirmSerializer(data=data)
        assert not serializer.is_valid()
        assert "confirm_new_password" in serializer.errors

    def test_weak_password(self, user_with_reset_code):
        """Test password reset with weak password."""
        data = {
            "email": "test@example.com",
            "otp": "12345678",
            "new_password": "weak",
            "confirm_new_password": "weak",
        }

        serializer = PasswordResetConfirmSerializer(data=data)
        assert not serializer.is_valid()
        # Password validation errors are in 'new_password' field
        assert (
            "new_password" in serializer.errors
            or "non_field_errors" in serializer.errors
        )

    def test_invalid_otp(self, user_with_reset_code):
        """Test password reset with invalid OTP."""
        data = {
            "email": "test@example.com",
            "otp": "87654321",
            "new_password": "NewPass123!",
            "confirm_new_password": "NewPass123!",
        }

        serializer = PasswordResetConfirmSerializer(data=data)
        assert not serializer.is_valid()
        assert "otp" in serializer.errors

    def test_expired_otp(self, user_with_reset_code):
        """Test password reset with expired OTP."""
        _, passcode = user_with_reset_code
        passcode.expires_at = timezone.now() - timedelta(minutes=10)
        passcode.save()

        data = {
            "email": "test@example.com",
            "otp": "12345678",
            "new_password": "NewPass123!",
            "confirm_new_password": "NewPass123!",
        }

        serializer = PasswordResetConfirmSerializer(data=data)
        assert not serializer.is_valid()
        assert "otp" in serializer.errors


@pytest.mark.django_db
class TestUserListSerializer:
    """Test suite for UserListSerializer."""

    def test_user_list_serialization_with_profile(self, create_user):
        """Test user list serialization with profile."""
        user = create_user()
        Profile.objects.create(user=user, bio="Test bio", country="USA")

        serializer = UserListSerializer(user)
        data = serializer.data

        assert data["email"] == "test@example.com"
        assert data["first_name"] == "John"
        assert data["profile"] is not None
        assert data["profile"]["bio"] == "Test bio"

    def test_user_list_serialization_without_profile(self, create_user):
        """Test user list serialization without profile."""
        user = create_user(email="noprofile@example.com")

        serializer = UserListSerializer(user)
        data = serializer.data

        assert data["email"] == "noprofile@example.com"
        assert data["profile"] is None
