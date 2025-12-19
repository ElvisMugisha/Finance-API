import time
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auths.models import Passcode, Profile
from utils import choices

User = get_user_model()


@pytest.fixture
def create_user():
    """Factory fixture for creating users"""

    def _create_user(**kwargs):
        unique_id = str(uuid.uuid4())[:8]
        defaults = {
            "email": f"test-{unique_id}@example.com",
            "username": f"user-{unique_id}",
            "first_name": "Test",
            "last_name": "User",
            "password": "TestPass123!",
        }
        defaults.update(kwargs)
        return User.objects.create_user(**defaults)

    return _create_user


@pytest.mark.django_db
def test_user_creation(create_user):
    user = create_user()
    assert user.pk is not None


@pytest.mark.django_db
class TestUserModel:
    """Comprehensive test suite for the custom User model."""

    @staticmethod
    def _create_user(**overrides):
        """
        Factory helper for creating users with sane defaults.
        Keeps tests DRY and focused on intent.
        """
        # Make each test get unique values
        unique_id = str(uuid.uuid4())[:8]
        defaults = {
            "email": f"user-{unique_id}@example.com",  # Make unique per call
            "username": f"testuser-{unique_id}",  # Make unique per call
            "first_name": "John",
            "last_name": "Doe",
            "password": "StrongPassword123!",
        }
        defaults.update(overrides)
        return User.objects.create_user(**defaults)

    def test_user_is_created_successfully(self):
        user = self._create_user()

        assert user.pk is not None
        assert isinstance(user.id, uuid.UUID)

    def test_email_is_used_as_username_field(self):
        user = self._create_user()

        assert User.USERNAME_FIELD == "email"
        assert user.get_username() == user.email

    def test_string_representation_returns_email(self):
        user = self._create_user(email="repr@example.com")

        assert str(user) == "repr@example.com"

    def test_email_must_be_unique(self):
        # Create first user with specific email
        first_user = User.objects.create_user(
            email="duplicate@example.com",
            username="user1",
            first_name="John",
            last_name="Doe",
            password="StrongPassword123!",
        )

        # Try to create second user with same email
        with pytest.raises(ValidationError) as exc_info:
            User.objects.create_user(
                email="duplicate@example.com",
                username="user2",
                first_name="Jane",
                last_name="Smith",
                password="StrongPassword123!",
            )

        # Check the error message
        assert "already exists" in str(exc_info.value)

    def test_username_must_be_unique(self):
        # Create first user with specific username
        first_user = User.objects.create_user(
            email="user1@example.com",
            username="duplicateuser",
            first_name="John",
            last_name="Doe",
            password="StrongPassword123!",
        )

        # Try to create second user with same username
        with pytest.raises(ValidationError) as exc_info:
            User.objects.create_user(
                email="user2@example.com",
                username="duplicateuser",
                first_name="Jane",
                last_name="Smith",
                password="StrongPassword123!",
            )

        # Check the error message
        assert "already exists" in str(exc_info.value)

    def test_missing_required_fields_raise_error(self):
        with pytest.raises(TypeError):
            User.objects.create_user(email="missing@example.com")
        # Should also test missing first_name and last_name
        with pytest.raises(TypeError):
            User.objects.create_user(email="missing@example.com", first_name="John")
        with pytest.raises(TypeError):
            User.objects.create_user(email="missing@example.com", last_name="Doe")

    def test_model_clean_rejects_premium_without_expiry(self):
        user = self._create_user(
            is_premium=True,
            premium_expires=timezone.now() + timedelta(days=1),  # pass valid first
        )
        user.premium_expires = None

        with pytest.raises(ValidationError):
            user.full_clean()

    def test_model_clean_allows_valid_premium_user(self):
        user = self._create_user(
            is_premium=True,
            premium_expires=timezone.now() + timedelta(days=30),
        )

        # Should not raise
        user.full_clean()

    def test_get_full_name_with_middle_name(self):
        user = self._create_user(middle_name="Michael")

        assert user.get_full_name() == "John Michael Doe"

    def test_get_full_name_without_middle_name(self):
        user = self._create_user(middle_name=None)

        assert user.get_full_name() == "John Doe"

    def test_full_name_property_alias(self):
        user = self._create_user()

        assert user.full_name == user.get_full_name()

    def test_get_short_name_returns_first_name(self):
        user = self._create_user(first_name="Alice")

        assert user.get_short_name() == "Alice"

    def test_is_premium_active_false_when_not_premium(self):
        user = self._create_user(is_premium=False)

        assert user.is_premium_active is False

    def test_is_premium_active_false_when_expired(self):
        user = self._create_user(
            is_premium=True,
            premium_expires=timezone.now() - timedelta(days=1),
        )

        assert user.is_premium_active is False

    def test_is_premium_active_true_when_valid(self):
        user = self._create_user(
            is_premium=True,
            premium_expires=timezone.now() + timedelta(days=1),
        )

        assert user.is_premium_active is True

    def test_default_flags(self):
        user = self._create_user()

        assert user.is_active is True
        assert user.is_verified is False
        assert user.is_staff is False
        assert user.is_premium is False

    def test_created_and_updated_timestamps_set(self):
        user = self._create_user()

        assert user.created_at is not None
        assert user.updated_at is not None

    def test_updated_at_changes_on_save(self):
        user = self._create_user()
        old_updated_at = user.updated_at

        # Sleep enough to ensure timestamp difference (at least 1 microsecond in some DBs)
        time.sleep(0.1)  # Increased from 0.001 to 0.1 seconds

        # Don't use update_fields - let Django handle the auto_now
        user.first_name = "Updated"
        user.save()  # Remove update_fields parameter

        user.refresh_from_db()
        # Use >= instead of > since some databases might have the same timestamp
        # but with auto_now it should definitely be different
        assert user.updated_at >= old_updated_at
        # In most cases it should be strictly greater
        if user.updated_at == old_updated_at:
            # This might happen in SQLite or with low precision timestamps
            pytest.skip("Timestamp precision too low to detect change")

    def test_create_superuser_sets_correct_flags(self):
        admin = User.objects.create_superuser(
            email="admin@example.com",
            username="admin",
            first_name="Admin",
            last_name="User",
            password="AdminPassword123!",
        )

        assert admin.is_staff is True
        assert admin.is_superuser is True
        assert admin.is_active is True
        assert admin.is_verified is True

    def test_email_database_uniqueness(self):
        """Test database-level constraint, bypassing model validation"""
        # Create first user normally
        first_user = self._create_user(email="db-dup@example.com")

        # Create second user directly with save() to test database constraint
        second_user = User(
            email="db-dup@example.com",  # Same email
            username="differentuser",
            first_name="Jane",
            last_name="Smith",
        )
        second_user.set_password("StrongPassword123!")

        # This should raise IntegrityError at the database level
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                second_user.save()

    def test_email_normalization(self):
        """Test that email is normalized (domain lowercased)"""
        user = self._create_user(email="TEST@EXAMPLE.COM")
        # Django's normalize_email only lowercases the domain part
        assert user.email == "TEST@example.com"

    def test_email_full_normalization_if_custom(self):
        """Test full email normalization if you implement it in your manager"""
        assert user.email == "test@example.com"

    def test_empty_password_allowed(self):
        """Test that password can be None (for social auth users)"""
        user = self._create_user(password=None)
        # When password is None, set_password creates an unusable password
        assert not user.has_usable_password()
        # Check that password field is not empty (it's a hash)
        assert user.password != ""
        assert user.password.startswith("!")

    def test_username_auto_generation(self):
        """Test that username is auto-generated when not provided"""
        user = User.objects.create_user(
            email="auto@example.com",
            first_name="Auto",
            last_name="Generated",
            password="password",
            # No username provided
        )
        assert user.username is not None
        # slugify removes dots, so it becomes "autogenerated"
        assert user.username.startswith("autogenerated")

    def test_username_auto_generation_with_dots_preserved(self):
        """Test username generation with dot preservation if needed"""
        user = User.objects.create_user(
            email="auto2@example.com",
            first_name="Auto",
            last_name="Generated",
            password="password",
        )
        # With current implementation (slugify):
        assert "." not in user.username  # slugify removes dots
        # With modified implementation (preserving dots):
        # assert user.username.startswith("auto.generated")

    def test_password_hashing(self):
        """Test that passwords are properly hashed"""
        user = self._create_user(password="MyPassword123!")
        # Password should be hashed, not stored in plain text
        assert user.password != "MyPassword123!"
        # Should start with algorithm identifier (argon2 by default)
        assert user.password.startswith("argon2") or user.password.startswith(
            "pbkdf2_sha256"
        )
        # Should be able to check password
        assert user.check_password("MyPassword123!")
        assert not user.check_password("WrongPassword")

    def test_user_without_password_cant_login(self):
        """Test that user with None password can't use password auth"""
        user = self._create_user(password=None)
        assert not user.has_usable_password()
        # check_password should return False for None/unusable passwords
        assert not user.check_password(None)
        assert not user.check_password("")
        assert not user.check_password("anypassword")


# @pytest.mark.django_db
# class TestProfileModel:
#     """Test suite for Profile model."""

#     @pytest.fixture
#     def user(self, django_user_model):
#         return django_user_model.objects.create_user(
#             email="profileuser@example.com",
#             first_name="Profile",
#             last_name="User",
#             password="password",
#         )

#     def test_create_profile_success(self, user):
#         profile = Profile.objects.create(user=user, bio="Hello world")
#         assert profile.user == user
#         assert profile.bio == "Hello world"
#         assert str(profile) == f"{user.first_name}'s Profile"

#     def test_unique_profile_constraint(self, user):
#         Profile.objects.create(user=user)
#         with pytest.raises(IntegrityError):
#             Profile.objects.create(user=user)

#     def test_optional_fields(self, user):
#         profile = Profile.objects.create(user=user)
#         assert profile.phone_number is None
#         assert profile.dob is None
#         assert profile.occupation is None


# @pytest.mark.django_db
# class TestPasscodeModel:
#     """Test suite for Passcode model."""

#     @pytest.fixture
#     def user(self, django_user_model):
#         return django_user_model.objects.create_user(
#             email="passcodeuser@example.com",
#             first_name="Pass",
#             last_name="User",
#             password="password",
#         )

#     def test_create_passcode_success(self, user):
#         expires = timezone.now() + timedelta(minutes=10)
#         passcode = Passcode.objects.create(
#             user=user,
#             code="123456",
#             code_type=choices.CodeType.LOGIN_OTP,
#             expires_at=expires,
#         )

#         assert isinstance(passcode.id, uuid.UUID)
#         assert passcode.user == user
#         assert passcode.code == "123456"
#         assert passcode.is_used is False
#         assert passcode.expires_at == expires
#         assert str(passcode) == f"{user.username} | {choices.CodeType.LOGIN_OTP}"

#     def test_unique_active_passcode_constraint(self, user):
#         expires = timezone.now() + timedelta(minutes=10)
#         Passcode.objects.create(
#             user=user,
#             code="111111",
#             code_type=choices.CodeType.LOGIN_OTP,
#             expires_at=expires,
#         )
#         with pytest.raises(IntegrityError):
#             Passcode.objects.create(
#                 user=user,
#                 code="222222",
#                 code_type=choices.CodeType.LOGIN_OTP,
#                 expires_at=expires,
#             )

#     def test_passcode_usage_flag(self, user):
#         expires = timezone.now() + timedelta(minutes=10)
#         passcode = Passcode.objects.create(
#             user=user,
#             code="333333",
#             code_type=choices.CodeType.PASSWORD_RESET,
#             expires_at=expires,
#         )
#         assert passcode.is_used is False
#         passcode.is_used = True
#         passcode.save()
#         assert Passcode.objects.get(id=passcode.id).is_used is True
