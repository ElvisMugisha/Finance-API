"""
Pytest configuration and fixtures for Finance-API tests.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def api_client():
    """Provide an API client for testing."""
    return APIClient()


@pytest.fixture
def user_data():
    """Provide valid user registration data."""
    return {
        "email": "test@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "password": "SecurePass123!",
        "confirm_password": "SecurePass123!",
    }


@pytest.fixture
def create_user(db):
    """Factory fixture to create users."""

    def make_user(**kwargs):
        defaults = {
            "email": "test@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "password": "SecurePass123!",
        }
        defaults.update(kwargs)
        return User.objects.create_user(**defaults)

    return make_user
