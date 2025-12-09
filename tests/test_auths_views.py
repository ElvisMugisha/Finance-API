"""
Comprehensive pytest tests for auths views.

This module contains integration tests for all view endpoints in the auths app,
testing the complete request-response cycle with Basic Authentication.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from auths.models import Passcode, Profile
from utils import choices

User = get_user_model()


# ============================================================================
# Fixtures
# ============================================================================
@pytest.fixture(autouse=True)
def disable_throttling(settings):
    """Disable throttling for all tests in this module."""
    if hasattr(settings, "REST_FRAMEWORK"):
        rf = settings.REST_FRAMEWORK.copy()
        rf["DEFAULT_THROTTLE_CLASSES"] = []
        rf["DEFAULT_THROTTLE_RATES"] = {}
        settings.REST_FRAMEWORK = rf


@pytest.fixture
def api_client():
    """Provide an API client for testing."""
    return APIClient()


@pytest.fixture
def create_user(db):
    """Factory fixture to create users."""

    def make_user(**kwargs):
        defaults = {
            "email": "test@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "password": "SecurePass123!",
            "is_verified": True,
            "is_active": True,
        }
        defaults.update(kwargs)
        password = defaults.pop("password")
        user = User.objects.create_user(**defaults)
        user.set_password(password)
        user.save()
        return user

    return make_user


@pytest.fixture
def authenticated_client(api_client, create_user):
    """Provide an authenticated API client with Basic Auth."""
    user = create_user()
    api_client.credentials(
        HTTP_AUTHORIZATION="Basic dGVzdEBleGFtcGxlLmNvbTpTZWN1cmVQYXNzMTIzIQ=="
    )
    return api_client, user


# ============================================================================
# UserRegistrationView Tests
# ============================================================================
@pytest.mark.django_db
class TestUserRegistrationView:
    """Test suite for UserRegistrationView."""

    def test_successful_registration(self, api_client, mocker):
        """Test successful user registration."""
        # Mock OTP sending to avoid actual email
        mocker.patch("utils.utils.send_code_to_user")

        url = reverse("register")
        data = {
            "email": "newuser@example.com",
            "first_name": "Jane",
            "last_name": "Smith",
            "password": "SecurePass123!",
            "confirm_password": "SecurePass123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert "message" in response.data
        assert User.objects.filter(email="newuser@example.com").exists()

    def test_registration_duplicate_email(self, api_client, create_user):
        """Test registration with duplicate email."""
        create_user(email="existing@example.com")

        url = reverse("register")
        data = {
            "email": "existing@example.com",
            "first_name": "Jane",
            "last_name": "Smith",
            "password": "SecurePass123!",
            "confirm_password": "SecurePass123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "email" in response.data

    def test_registration_password_mismatch(self, api_client):
        """Test registration with mismatched passwords."""
        url = reverse("register")
        data = {
            "email": "newuser@example.com",
            "first_name": "Jane",
            "last_name": "Smith",
            "password": "SecurePass123!",
            "confirm_password": "DifferentPass123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "password" in response.data


# ============================================================================
# LoginView Tests
# ============================================================================
@pytest.mark.django_db
class TestLoginView:
    """Test suite for LoginView."""

    def test_successful_login(self, api_client, create_user):
        """Test successful login with valid credentials."""
        create_user(email="user@example.com", password="SecurePass123!")

        url = reverse("login")
        data = {"email": "user@example.com", "password": "SecurePass123!"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert "user" in response.data
        assert "authentication" in response.data

    def test_login_invalid_credentials(self, api_client, create_user):
        """Test login with invalid credentials."""
        create_user(email="user@example.com", password="SecurePass123!")

        url = reverse("login")
        data = {"email": "user@example.com", "password": "WrongPassword!"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data

    def test_login_unverified_user(self, api_client, create_user):
        """Test login with unverified account."""
        create_user(
            email="unverified@example.com",
            password="SecurePass123!",
            is_verified=False,
        )

        url = reverse("login")
        data = {"email": "unverified@example.com", "password": "SecurePass123!"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "not verified" in response.data["error"].lower()

    def test_login_inactive_user(self, api_client, create_user):
        """Test login with inactive account."""
        create_user(
            email="inactive@example.com",
            password="SecurePass123!",
            is_active=False,
            is_verified=True,  # Ensure verified so only inactive check fails
        )

        url = reverse("login")
        data = {"email": "inactive@example.com", "password": "SecurePass123!"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data


# ============================================================================
# LogoutView Tests
# ============================================================================
@pytest.mark.django_db
class TestLogoutView:
    """Test suite for LogoutView."""

    def test_successful_logout(self, authenticated_client):
        """Test successful logout."""
        api_client, user = authenticated_client

        url = reverse("logout")
        response = api_client.post(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_logout_unauthenticated(self, api_client):
        """Test logout without authentication."""
        url = reverse("logout")
        response = api_client.post(url, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ============================================================================
# EmailVerificationView Tests
# ============================================================================
@pytest.mark.django_db
class TestEmailVerificationView:
    """Test suite for EmailVerificationView."""

    def test_successful_verification(self, api_client, create_user):
        """Test successful email verification."""
        user = create_user(is_verified=False)
        Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.VERIFICATION,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        url = reverse("verify-email")
        data = {"email": user.email, "otp": "12345678"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.is_verified is True

    def test_verification_invalid_otp(self, api_client, create_user):
        """Test verification with invalid OTP."""
        user = create_user(is_verified=False)

        url = reverse("verify-email")
        data = {"email": user.email, "otp": "99999999"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verification_expired_otp(self, api_client, create_user):
        """Test verification with expired OTP."""
        user = create_user(is_verified=False)
        Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.VERIFICATION,
            expires_at=timezone.now() - timedelta(minutes=10),
        )

        url = reverse("verify-email")
        data = {"email": user.email, "otp": "12345678"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_410_GONE


# ============================================================================
# ResendOTPView Tests
# ============================================================================
@pytest.mark.django_db
class TestResendOTPView:
    """Test suite for ResendOTPView."""

    def test_successful_resend(self, api_client, create_user, mocker):
        """Test successful OTP resend."""
        mocker.patch("utils.utils.send_code_to_user")
        user = create_user(is_verified=False)

        url = reverse("resend-otp")
        data = {"email": user.email}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert "message" in response.data

    def test_resend_already_verified(self, api_client, create_user):
        """Test OTP resend for already verified user."""
        user = create_user(is_verified=True)

        url = reverse("resend-otp")
        data = {"email": user.email}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# UserListView Tests
# ============================================================================
@pytest.mark.django_db
class TestUserListView:
    """Test suite for UserListView."""

    def test_list_users_as_superuser(self, api_client, create_user):
        """Test listing users as superuser."""
        superuser = create_user(email="admin@example.com", is_superuser=True)
        create_user(email="user1@example.com")
        create_user(email="user2@example.com")

        # Authenticate as superuser
        api_client.force_authenticate(user=superuser)

        url = reverse("user-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "data" in response.data
        assert len(response.data["data"]) >= 3

    def test_list_users_as_regular_user(self, authenticated_client):
        """Test listing users as regular user (should fail)."""
        api_client, user = authenticated_client

        url = reverse("user-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN


# ============================================================================
# UserProfileView Tests
# ============================================================================
@pytest.mark.django_db
class TestUserProfileView:
    """Test suite for UserProfileView."""

    def test_get_profile(self, authenticated_client):
        """Test getting user profile."""
        api_client, user = authenticated_client

        url = reverse("user-profile")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["email"] == user.email

    def test_get_profile_unauthenticated(self, api_client):
        """Test getting profile without authentication."""
        url = reverse("user-profile")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ============================================================================
# UserProfileManageView Tests
# ============================================================================
@pytest.mark.django_db
class TestUserProfileManageView:
    """Test suite for UserProfileManageView."""

    def test_create_profile(self, authenticated_client):
        """Test creating user profile."""
        api_client, user = authenticated_client

        url = reverse("manage-profile")
        data = {
            "bio": "Test bio",
            "phone_number": "+1234567890",
            "gender": choices.Gender.MALE,
            "country": "USA",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Profile.objects.filter(user=user).exists()

    def test_update_profile(self, authenticated_client):
        """Test updating existing profile."""
        api_client, user = authenticated_client
        Profile.objects.create(user=user, bio="Old bio")

        url = reverse("manage-profile")
        data = {"bio": "Updated bio"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        profile = Profile.objects.get(user=user)
        assert profile.bio == "Updated bio"


# ============================================================================
# PasswordChangeView Tests
# ============================================================================
@pytest.mark.django_db
class TestPasswordChangeView:
    """Test suite for PasswordChangeView."""

    def test_successful_password_change(self, authenticated_client):
        """Test successful password change."""
        api_client, user = authenticated_client

        url = reverse("change-password")
        data = {
            "old_password": "SecurePass123!",
            "new_password": "NewSecurePass123!",
            "confirm_new_password": "NewSecurePass123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password("NewSecurePass123!")

    def test_password_change_wrong_old_password(self, authenticated_client):
        """Test password change with wrong old password."""
        api_client, user = authenticated_client

        url = reverse("change-password")
        data = {
            "old_password": "WrongPassword!",
            "new_password": "NewSecurePass123!",
            "confirm_new_password": "NewSecurePass123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_change_mismatch(self, authenticated_client):
        """Test password change with mismatched new passwords."""
        api_client, user = authenticated_client

        url = reverse("change-password")
        data = {
            "old_password": "SecurePass123!",
            "new_password": "NewSecurePass123!",
            "confirm_new_password": "DifferentPass123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ============================================================================
# Password Reset Flow Tests
# ============================================================================
@pytest.mark.django_db
class TestPasswordResetFlow:
    """Test suite for password reset flow."""

    def test_password_reset_request(self, api_client, create_user, mocker):
        """Test password reset request."""
        mocker.patch("utils.utils.send_code_to_user")
        user = create_user()

        url = reverse("password-reset-request")
        data = {"email": user.email}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert Passcode.objects.filter(
            user=user, code_type=choices.CodeType.PASSWORD_RESET
        ).exists()

    def test_password_reset_verify(self, api_client, create_user):
        """Test password reset OTP verification."""
        user = create_user()
        Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        url = reverse("password-reset-verify")
        data = {"email": user.email, "otp": "12345678"}

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK

    def test_password_reset_confirm(self, api_client, create_user, mocker):
        """Test password reset confirmation."""
        mocker.patch("utils.utils.send_normal_email")
        user = create_user()
        Passcode.objects.create(
            user=user,
            code="12345678",
            code_type=choices.CodeType.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        url = reverse("password-reset-confirm")
        data = {
            "email": user.email,
            "otp": "12345678",
            "new_password": "NewPassword123!",
            "confirm_new_password": "NewPassword123!",
        }

        response = api_client.post(url, data, format="json")

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password("NewPassword123!")

    def test_complete_password_reset_flow(self, api_client, create_user, mocker):
        """Test complete password reset flow."""
        mocker.patch("utils.utils.send_code_to_user")
        mocker.patch("utils.utils.send_normal_email")

        user = create_user(password="OldPassword123!")

        # Step 1: Request password reset
        url = reverse("password-reset-request")
        response = api_client.post(url, {"email": user.email}, format="json")
        assert response.status_code == status.HTTP_200_OK

        # Get the OTP
        passcode = Passcode.objects.get(
            user=user, code_type=choices.CodeType.PASSWORD_RESET
        )

        # Step 2: Verify OTP
        url = reverse("password-reset-verify")
        response = api_client.post(
            url, {"email": user.email, "otp": passcode.code}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

        # Step 3: Confirm new password
        url = reverse("password-reset-confirm")
        response = api_client.post(
            url,
            {
                "email": user.email,
                "otp": passcode.code,
                "new_password": "NewPassword123!",
                "confirm_new_password": "NewPassword123!",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

        # Verify password was changed
        user.refresh_from_db()
        assert user.check_password("NewPassword123!")
        assert not user.check_password("OldPassword123!")
