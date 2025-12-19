from drf_spectacular.utils import OpenApiExample

PROFILE_REQUEST_EXAMPLE = OpenApiExample(
    name="Profile Upsert Request",
    summary="Create or update user profile",
    description="Example payload for creating or partially updating a user profile.",
    value={
        "bio": "Senior backend engineer with a focus on APIs",
        "phone_number": "+250788123456",
        "date_of_birth": "1998-04-10",
        "gender": "Male",
        "occupation": "Software Engineer",
        "annual_income": "6000000",
        "currency_preference": "RWF",
        "notification_preferences": {"email": True, "sms": False},
        "privacy_settings": {"show_email": False, "show_phone": False},
    },
    request_only=True,
)

PROFILE_RESPONSE_EXAMPLE = OpenApiExample(
    name="Profile Response",
    summary="Profile successfully created or updated",
    description="Returned profile data after successful upsert.",
    value={
        "bio": "Senior backend engineer with a focus on APIs",
        "phone_number": "+250788123456",
        "date_of_birth": "1998-04-10",
        "profile_picture": "/media/profiles/2025/12/18/avatar.jpg",
        "gender": "Male",
        "occupation": "Software Engineer",
        "annual_income": "6000000.00",
        "currency_preference": "RWF",
        "notification_preferences": {"email": True, "sms": False},
        "privacy_settings": {"show_email": False, "show_phone": False},
        "created_at": "2025-12-18T07:22:23.776161Z",
        "updated_at": "2025-12-19T10:15:01.221093Z",
    },
    response_only=True,
)

PROFILE_VALIDATION_ERROR_EXAMPLE = OpenApiExample(
    name="Validation Error",
    summary="Invalid profile data",
    description="Returned when validation fails.",
    value={"annual_income": ["Annual income must be a non-negative amount."]},
    response_only=True,
)


USER_PROFILE_RESPONSE_EXAMPLE = OpenApiExample(
    name="User Profile Response",
    summary="Authenticated user with profile",
    value={
        "id": "251d5e4f-4694-48c9-9e8e-58b86c31e451",
        "email": "admin@example.com",
        "username": "admin",
        "first_name": "Elvis",
        "middle_name": "John",
        "last_name": "Admin",
        "is_premium": False,
        "is_premium_active": False,
        "last_login": None,
        "last_activity_at": None,
        "created_at": "2025-12-18T10:01:25.026412Z",
        "updated_at": "2025-12-18T10:01:25.026445Z",
        "profile": {
            "bio": "Site administration",
            "phone_number": "+2507815273973",
            "date_of_birth": "1998-04-10",
            "gender": "Male",
            "occupation": "Software Engineer",
            "annual_income": "6000000.00",
            "currency_preference": "RWF",
            "notification_preferences": {},
            "privacy_settings": {},
            "created_at": "2025-12-18T07:22:23.776161Z",
            "updated_at": "2025-12-18T07:22:23.776178Z",
        },
    },
    response_only=True,
)
