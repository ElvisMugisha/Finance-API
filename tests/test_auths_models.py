import uuid
from datetime import datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from auths.models import Passcode, Profile, User
from utils import choices


@pytest.mark.django_db
class TestUserModel:
    """Test suite for the custom User model."""

    def test_create_user_success(self, django_user_model):
        user = django_user_model.objects.create_user(
            email="testuser@example.com",
            first_name="Test",
            last_name="User",
            password="securepassword",
        )

        assert isinstance(user.id, uuid.UUID)
        assert user.email == "testuser@example.com"
        assert user.first_name == "Test"
        assert user.last_name == "User"
        assert user.is_active is True
        assert user.is_verified is False
        assert user.check_password("securepassword") is True
        assert str(user) == user.email
        assert user.full_name == "Test User"

    def test_create_user_missing_email_fails(self, django_user_model):
        with pytest.raises(ValueError) as exc:
            django_user_model.objects.create_user(
                email=None, first_name="Test", last_name="User", password="password"
            )
        assert "email" in str(exc.value)

    def test_create_user_missing_first_name_fails(self, django_user_model):
        with pytest.raises(ValueError) as exc:
            django_user_model.objects.create_user(
                email="test@example.com",
                first_name="",
                last_name="User",
                password="password",
            )
        assert "First name" in str(exc.value)

    def test_create_user_missing_last_name_fails(self, django_user_model):
        with pytest.raises(ValueError) as exc:
            django_user_model.objects.create_user(
                email="test@example.com",
                first_name="Test",
                last_name="",
                password="password",
            )
        assert "Last name" in str(exc.value)

    def test_create_superuser_success(self, django_user_model):
        superuser = django_user_model.objects.create_superuser(
            email="admin@example.com",
            first_name="Admin",
            last_name="User",
            password="adminpassword",
        )

        assert superuser.is_superuser is True
        assert superuser.is_staff is True
        assert superuser.is_active is True
        assert superuser.is_verified is True

    def test_generate_username_uniqueness(self, django_user_model):
        user1 = django_user_model.objects.create_user(
            email="user1@example.com",
            first_name="John",
            last_name="Doe",
            password="password",
        )
        user2 = django_user_model.objects.create_user(
            email="user2@example.com",
            first_name="John",
            last_name="Doe",
            password="password",
        )

        assert user1.username != user2.username
        assert user2.username.startswith(user1.username)

    def test_str_and_full_name(self, django_user_model):
        user = django_user_model.objects.create_user(
            email="a@example.com", first_name="A", last_name="B", password="password"
        )
        assert str(user) == "a@example.com"
        assert user.full_name == "A B"


@pytest.mark.django_db
class TestProfileModel:
    """Test suite for Profile model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="profileuser@example.com",
            first_name="Profile",
            last_name="User",
            password="password",
        )

    def test_create_profile_success(self, user):
        profile = Profile.objects.create(user=user, bio="Hello world")
        assert profile.user == user
        assert profile.bio == "Hello world"
        assert str(profile) == f"{user.first_name}'s Profile"

    def test_unique_profile_constraint(self, user):
        Profile.objects.create(user=user)
        with pytest.raises(IntegrityError):
            Profile.objects.create(user=user)

    def test_optional_fields(self, user):
        profile = Profile.objects.create(user=user)
        assert profile.phone_number is None
        assert profile.dob is None
        assert profile.occupation is None


@pytest.mark.django_db
class TestPasscodeModel:
    """Test suite for Passcode model."""

    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(
            email="passcodeuser@example.com",
            first_name="Pass",
            last_name="User",
            password="password",
        )

    def test_create_passcode_success(self, user):
        expires = timezone.now() + timedelta(minutes=10)
        passcode = Passcode.objects.create(
            user=user,
            code="123456",
            code_type=choices.CodeType.LOGIN_OTP,
            expires_at=expires,
        )

        assert isinstance(passcode.id, uuid.UUID)
        assert passcode.user == user
        assert passcode.code == "123456"
        assert passcode.is_used is False
        assert passcode.expires_at == expires
        assert str(passcode) == f"{user.username} | {choices.CodeType.LOGIN_OTP}"

    def test_unique_active_passcode_constraint(self, user):
        expires = timezone.now() + timedelta(minutes=10)
        Passcode.objects.create(
            user=user,
            code="111111",
            code_type=choices.CodeType.LOGIN_OTP,
            expires_at=expires,
        )
        with pytest.raises(IntegrityError):
            Passcode.objects.create(
                user=user,
                code="222222",
                code_type=choices.CodeType.LOGIN_OTP,
                expires_at=expires,
            )

    def test_passcode_usage_flag(self, user):
        expires = timezone.now() + timedelta(minutes=10)
        passcode = Passcode.objects.create(
            user=user,
            code="333333",
            code_type=choices.CodeType.PASSWORD_RESET,
            expires_at=expires,
        )
        assert passcode.is_used is False
        passcode.is_used = True
        passcode.save()
        assert Passcode.objects.get(id=passcode.id).is_used is True
