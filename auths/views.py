from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .serializers import (
    UserRegistrationSerializer,
    LoginSerializer,
    UserListSerializer,
    EmailVerificationSerializer,
    ResendOTPSerializer,
    ProfileSerializer,
    PasswordChangeSerializer,
    PasswordResetRequestSerializer,
    PasswordResetVerifySerializer,
    PasswordResetConfirmSerializer,
)
from .models import User
from utils import loggings, choices
from utils.permissions import IsActiveAndVerified, IsSuperAdminOrSuperUser
from utils.utils import create_and_send_otp
from utils.paginations import CustomPageNumberPagination

# Initialize logger
logger = loggings.setup_logging()


class UserRegistrationView(APIView):
    """
    API View for User Registration.

    Handles the creation of a new user account, generation of a verification passcode,
    and sending the passcode via email.
    """

    permission_classes = [AllowAny]
    serializer_class = UserRegistrationSerializer

    @extend_schema(
        summary="Register a new user",
        description="Create a new user account and send a verification email.",
        request=UserRegistrationSerializer,
        responses={
            201: UserRegistrationSerializer,
            400: OpenApiResponse(description="Bad Request"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to register a new user.

        Steps:
        1. Validate input data.
        2. Create user.
        3. Generate and send OTP.
        4. If OTP/Email fails, rollback (delete) user.
        """
        logger.info("Received user registration request.")

        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            user = None
            try:
                # Save the user
                user = serializer.save()
                logger.info(f"User created successfully: {user.email}")

                # Create and send OTP
                otp, error_msg, error_status = create_and_send_otp(
                    user=user,
                    code_type=choices.CodeType.VERIFICATION,
                    purpose="verification",
                )

                if error_msg:
                    logger.error(f"Failed to send OTP to {user.email}: {error_msg}")
                    # Delete the user if OTP sending fails (Rollback)
                    user.delete()
                    logger.info(f"Rolled back user creation for {user.email}")
                    return Response({"error": error_msg}, status=error_status)

                logger.info(
                    f"Registration flow completed successfully for {user.email}"
                )
                return Response(
                    {
                        "message": "Your account has been created successfully. Please check your email for the verification code.",
                        "data": serializer.data,
                    },
                    status=status.HTTP_201_CREATED,
                )

            except Exception as e:
                logger.exception(f"Unexpected error during registration: {str(e)}")
                # Ensure user is deleted if an unexpected exception occurs after creation
                if user:
                    user.delete()
                    logger.info(
                        f"Rolled back user creation for {user.email} due to exception"
                    )

                return Response(
                    {"error": "An unexpected error occurred. Please try again later."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        logger.warning(f"Registration validation failed: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    """
    API View for User Login with Multi-Authentication Support.

    Supports multiple authentication methods:
    - JWT Authentication (access + refresh tokens)
    - Token Authentication (DRF token)
    - Session Authentication (automatic via cookies)
    - Basic Authentication (no login required)

    Returns all necessary tokens, allowing clients to choose their preferred method.
    """

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    @extend_schema(
        summary="User Login - Multi-Auth Support",
        description=(
            "Authenticate user and return multiple authentication tokens.\n\n"
            "**Supported Authentication Methods:**\n"
            "- **JWT**: Use access token with 'Authorization: JWT <access_token>'\n"
            "- **Token**: Use token with 'Authorization: Token <token_key>'\n"
            "- **Session**: Automatic via session cookie (for browsers)\n"
            "- **Basic**: Send credentials with each request (no login needed)\n\n"
            "**Response includes:**\n"
            "- JWT access token (short-lived, 3 hours)\n"
            "- JWT refresh token (long-lived, 2 days)\n"
            "- DRF authentication token\n"
            "- User information"
        ),
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(
                description="Login successful - Returns JWT and Token authentication credentials"
            ),
            400: OpenApiResponse(description="Bad Request - Invalid credentials"),
            403: OpenApiResponse(
                description="Forbidden - Account not verified or inactive"
            ),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request for user login.

        Generates and returns multiple authentication tokens for maximum flexibility.

        Args:
            request: HTTP request containing email and password.

        Returns:
            Response containing:
                - JWT tokens (access and refresh)
                - DRF Token
                - User information
                - Authentication method instructions

        Raises:
            ValidationError: If credentials are invalid
            PermissionDenied: If account is not verified or inactive
        """
        logger.info("Received login request")

        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )

        if serializer.is_valid():
            try:
                user = serializer.validated_data["user"]
                logger.debug(f"User to be authenticated: {user.email}")

                # Validation: Check if user is verified
                if not user.is_verified:
                    logger.warning(f"Login attempt by unverified user: {user.email}")
                    return Response(
                        {
                            "error": "Account not verified",
                            "message": "Please verify your email address before logging in.",
                            "email": user.email,
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

                # Validation: Check if user is active
                if not user.is_active:
                    logger.warning(f"Login attempt by inactive user: {user.email}")
                    return Response(
                        {
                            "error": "Account inactive",
                            "message": "Your account has been deactivated. Please contact support.",
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

                logger.info(f"Generating authentication tokens for user: {user.email}")

                # ============================================================
                # 1. JWT Authentication - Generate access and refresh tokens
                # ============================================================
                try:
                    from rest_framework_simplejwt.tokens import RefreshToken

                    refresh = RefreshToken.for_user(user)
                    access_token = str(refresh.access_token)
                    refresh_token = str(refresh)

                    logger.debug(f"JWT tokens generated successfully for {user.email}")
                except Exception as jwt_error:
                    logger.error(
                        f"Failed to generate JWT tokens for {user.email}: {str(jwt_error)}"
                    )
                    # Continue without JWT if it fails
                    access_token = None
                    refresh_token = None

                # ============================================================
                # 2. Token Authentication - Generate or retrieve DRF token
                # ============================================================
                try:
                    from rest_framework.authtoken.models import Token

                    drf_token, created = Token.objects.get_or_create(user=user)
                    token_action = "created" if created else "retrieved"
                    logger.debug(
                        f"DRF Token {token_action} successfully for {user.email}"
                    )
                except Exception as token_error:
                    logger.error(
                        f"Failed to generate DRF token for {user.email}: {str(token_error)}"
                    )
                    drf_token = None

                # ============================================================
                # 3. Session Authentication - Handled automatically by Django
                # ============================================================
                # Django will set session cookie automatically if SessionAuthentication
                # is in DEFAULT_AUTHENTICATION_CLASSES and client supports cookies

                # ============================================================
                # 4. Basic Authentication - No token needed
                # ============================================================
                # Clients send credentials with each request

                # Ensure at least one authentication method succeeded
                if not access_token and not drf_token:
                    logger.error(
                        f"All token generation methods failed for {user.email}"
                    )
                    return Response(
                        {
                            "error": "Authentication token generation failed",
                            "message": "Unable to generate authentication tokens. Please try again.",
                        },
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                logger.info(f"User logged in successfully: {user.email}")

                # Prepare comprehensive response
                response_data = {
                    "success": True,
                    "message": "Login successful",
                    "authentication": {
                        "jwt": (
                            {
                                "access": access_token,
                                "refresh": refresh_token,
                                "type": "Bearer",
                                "expires_in": 10800,  # 3 hours in seconds
                            }
                            if access_token
                            else None
                        ),
                        "token": (
                            {
                                "key": drf_token.key,
                                "type": "Token",
                            }
                            if drf_token
                            else None
                        ),
                        "session": {
                            "enabled": True,
                            "info": "Session cookie set automatically for browser clients",
                        },
                    },
                    "user": {
                        "id": str(user.id),
                        "username": user.username,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "full_name": user.full_name,
                        "is_verified": user.is_verified,
                        "is_active": user.is_active,
                        "last_activity": user.last_activity,
                    },
                    "usage_instructions": {
                        "jwt": "Include header: Authorization: JWT <access_token>",
                        "token": "Include header: Authorization: Token <token_key>",
                        "session": "Session cookie sent automatically by browser",
                        "basic": "Include header: Authorization: Basic <base64(email:password)>",
                    },
                }

                return Response(response_data, status=status.HTTP_200_OK)

            except KeyError as ke:
                logger.error(f"Missing key in validated data: {str(ke)}")
                return Response(
                    {
                        "error": "Invalid response data",
                        "message": "An error occurred during authentication.",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            except Exception as e:
                logger.exception(f"Unexpected error during login: {str(e)}")
                return Response(
                    {
                        "error": "Login failed",
                        "message": "An unexpected error occurred. Please try again later.",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Serializer validation failed
        logger.warning(f"Login validation failed: {serializer.errors}")
        return Response(
            {"error": "Invalid credentials", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )


class LogoutView(APIView):
    """
    API View for User Logout with Multi-Authentication Support.

    Handles logout for all configured authentication methods:
    - JWT Authentication: Blacklists refresh token
    - Token Authentication: Deletes DRF token
    - Session Authentication: Clears Django session
    - Basic Authentication: No action needed (stateless)

    Ensures complete logout across all authentication methods.
    """

    permission_classes = [IsActiveAndVerified]
    serializer_class = None  # No input serializer required

    @extend_schema(
        summary="User Logout - Multi-Auth Support",
        description=(
            "Logout user by invalidating all authentication tokens and sessions.\n\n"
            "**Actions performed:**\n"
            "- Blacklists JWT refresh token (if provided)\n"
            "- Deletes DRF authentication token\n"
            "- Clears Django session\n"
            "- Returns summary of logout actions\n\n"
            "**Optional Request Body:**\n"
            "```json\n"
            "{\n"
            '  "refresh_token": "your_jwt_refresh_token_here"\n'
            "}\n"
            "```"
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "refresh_token": {
                        "type": "string",
                        "description": "JWT refresh token to blacklist (optional but recommended)",
                    }
                },
                "example": {"refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGc..."},
            }
        },
        responses={
            200: OpenApiResponse(
                description="Logout successful - All tokens invalidated"
            ),
            401: OpenApiResponse(description="Unauthorized - Not authenticated"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request for user logout.

        Invalidates all authentication tokens and clears session.

        Args:
            request: HTTP request, optionally containing refresh_token in body.

        Returns:
            Response: Success message with details of logout actions performed.

        Note:
            Even if some logout actions fail, the endpoint returns success
            as long as at least one method succeeds.
        """
        logger.info(f"Logout requested by user: {request.user.email}")

        logout_actions = []
        logout_errors = []

        try:
            # ============================================================
            # 1. JWT Authentication - Blacklist refresh token
            # ============================================================
            refresh_token = request.data.get("refresh_token")
            if refresh_token:
                try:
                    from rest_framework_simplejwt.tokens import RefreshToken

                    token = RefreshToken(refresh_token)
                    token.blacklist()
                    logout_actions.append("JWT refresh token blacklisted")
                    logger.info(
                        f"JWT refresh token blacklisted successfully for {request.user.email}"
                    )
                except Exception as jwt_error:
                    error_msg = f"JWT token blacklisting failed: {str(jwt_error)}"
                    logout_errors.append(error_msg)
                    logger.warning(
                        f"Failed to blacklist JWT token for {request.user.email}: {str(jwt_error)}"
                    )
            else:
                logger.debug(
                    f"No JWT refresh token provided for logout by {request.user.email}"
                )
                logout_actions.append(
                    "JWT refresh token not provided (access token will expire naturally)"
                )

            # ============================================================
            # 2. Token Authentication - Delete DRF token
            # ============================================================
            try:
                if hasattr(request.user, "auth_token"):
                    request.user.auth_token.delete()
                    logout_actions.append("DRF authentication token deleted")
                    logger.info(
                        f"DRF Token deleted successfully for {request.user.email}"
                    )
                else:
                    logger.debug(f"No DRF Token found for user: {request.user.email}")
                    logout_actions.append("No DRF token found")
            except Exception as token_error:
                error_msg = f"DRF token deletion failed: {str(token_error)}"
                logout_errors.append(error_msg)
                logger.error(
                    f"Failed to delete DRF token for {request.user.email}: {str(token_error)}"
                )

            # ============================================================
            # 3. Session Authentication - Clear Django session
            # ============================================================
            try:
                if hasattr(request, "session") and request.session.session_key:
                    from django.contrib.auth import logout as django_logout

                    django_logout(request)
                    logout_actions.append("Django session cleared")
                    logger.info(
                        f"Session cleared successfully for {request.user.email}"
                    )
                else:
                    logger.debug(
                        f"No active session found for user: {request.user.email}"
                    )
                    logout_actions.append("No active session found")
            except Exception as session_error:
                error_msg = f"Session clearing failed: {str(session_error)}"
                logout_errors.append(error_msg)
                logger.error(
                    f"Failed to clear session for {request.user.email}: {str(session_error)}"
                )

            # ============================================================
            # 4. Basic Authentication - No action needed
            # ============================================================
            # Basic auth is stateless, credentials sent with each request

            # Prepare response
            if logout_actions:
                logger.info(
                    f"User logged out successfully: {request.user.email}. "
                    f"Actions: {', '.join(logout_actions)}"
                )

                response_data = {
                    "success": True,
                    "message": "Successfully logged out from all authentication methods.",
                    "actions_performed": logout_actions,
                }

                if logout_errors:
                    response_data["warnings"] = logout_errors
                    logger.warning(
                        f"Logout completed with warnings for {request.user.email}: {logout_errors}"
                    )

                return Response(response_data, status=status.HTTP_200_OK)
            else:
                # No actions performed (unlikely but handle gracefully)
                logger.warning(
                    f"Logout called but no actions performed for {request.user.email}"
                )
                return Response(
                    {
                        "success": True,
                        "message": "No active authentication tokens found.",
                        "actions_performed": ["No tokens to invalidate"],
                    },
                    status=status.HTTP_200_OK,
                )

        except Exception as e:
            logger.exception(
                f"Unexpected error during logout for {request.user.email}: {str(e)}"
            )
            return Response(
                {
                    "error": "Logout failed",
                    "message": "An unexpected error occurred during logout. Please try again.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserListView(APIView):
    """
    API View for listing all users.

    Only Super Admins and Superusers can access this view.
    Returns paginated list of users with their profile information.
    """

    permission_classes = [IsSuperAdminOrSuperUser]
    serializer_class = UserListSerializer
    pagination_class = CustomPageNumberPagination

    @extend_schema(
        summary="List all users",
        description="Retrieve a paginated list of all users with their profile information. Only accessible by Super Admins and Superusers.",
        responses={
            200: UserListSerializer(many=True),
            403: OpenApiResponse(description="Forbidden - Insufficient permissions"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def get(self, request):
        """
        Handle GET request to list all users.

        Returns:
            Paginated list of users with profile data.
        """
        logger.info(f"User list requested by: {request.user.email}")

        try:
            # Get all users and prefetch related profile data for optimization
            # Use prefetch_related for reverse OneToOne relationship
            queryset = (
                User.objects.prefetch_related("user_profile")
                .all()
                .order_by("-created_at")
            )

            # Apply pagination
            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            # Serialize the data
            serializer = self.serializer_class(paginated_queryset, many=True)

            logger.info(
                f"Successfully retrieved {len(serializer.data)} users for {request.user.email}"
            )

            # Return paginated response
            return paginator.get_paginated_response(serializer.data)

        except Exception as e:
            logger.exception(f"Error retrieving user list: {str(e)}")
            return Response(
                {"error": "An error occurred while retrieving users."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserProfileView(APIView):
    """
    API View for retrieving the current user's profile.

    Returns the user's information along with their profile data.
    If the profile does not exist, the profile field will be null.
    """

    permission_classes = [IsActiveAndVerified]
    serializer_class = UserListSerializer

    @extend_schema(
        summary="Get user profile",
        description="Retrieve the authenticated user's information and profile data.",
        responses={
            200: UserListSerializer,
            401: OpenApiResponse(description="Unauthorized"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def get(self, request):
        """
        Handle GET request to retrieve user profile.

        Returns:
            User data with profile information.
        """
        logger.info(f"User profile requested by: {request.user.email}")

        try:
            # The user is already available in request.user
            # We use the serializer to format the response
            serializer = self.serializer_class(request.user)

            logger.info(f"Successfully retrieved profile for {request.user.email}")
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Error retrieving user profile: {str(e)}")
            return Response(
                {"error": "An error occurred while retrieving your profile."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EmailVerificationView(APIView):
    """
    API View for Email Verification.

    Handles the verification of a user's email address using a one-time passcode (OTP).
    Upon successful verification, the user's account is marked as verified and the OTP
    is marked as used to prevent reuse.
    """

    permission_classes = [AllowAny]
    serializer_class = EmailVerificationSerializer

    @extend_schema(
        summary="Verify user email",
        description="Verify a user's email address using the OTP sent during registration.",
        request=EmailVerificationSerializer,
        responses={
            200: OpenApiResponse(description="Email verified successfully"),
            400: OpenApiResponse(description="Bad Request - Invalid data or OTP"),
            404: OpenApiResponse(description="User not found"),
            410: OpenApiResponse(description="OTP expired"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to verify user email.

        Steps:
        1. Validate input data (email and OTP).
        2. Verify OTP is valid, not expired, and not used.
        3. Mark user as verified.
        4. Mark OTP as used.
        5. Return success response.

        Args:
            request: HTTP request containing email and otp.

        Returns:
            Response: Success or error message with appropriate status code.
        """
        logger.info("Received email verification request")

        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            try:
                # Extract validated data
                validated_data = serializer.validated_data
                user = validated_data["user"]
                passcode = validated_data["passcode"]

                logger.info(f"Processing email verification for user: {user.email}")

                # Use database transaction to ensure atomicity
                from django.db import transaction

                try:
                    with transaction.atomic():
                        # Mark the user as verified
                        user.is_verified = True
                        user.save(update_fields=["is_verified"])
                        logger.info(f"User {user.email} marked as verified")

                        # Mark the passcode as used
                        passcode.is_used = True
                        passcode.save(update_fields=["is_used"])
                        logger.info(f"Passcode marked as used for user {user.email}")

                except Exception as db_error:
                    logger.exception(
                        f"Database error during verification: {str(db_error)}"
                    )
                    return Response(
                        {
                            "error": "Failed to update verification status. Please try again."
                        },
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                # Send success email notification (optional)
                try:
                    from utils.utils import send_normal_email

                    email_data = {
                        "to_email": user.email,
                        "email_subject": "Email Verification Successful",
                        "email_body": (
                            f"Hi {user.first_name},\n\n"
                            f"Your email has been successfully verified!\n\n"
                            f"You can now access all features of your AutoDocAI account.\n\n"
                            f"If you didn't perform this action, please contact our support team immediately.\n\n"
                            f"Best regards,\n"
                            f"AutoDocAI Team"
                        ),
                    }
                    send_normal_email(email_data)
                    logger.info(f"Verification success email sent to {user.email}")
                except Exception as email_error:
                    # Log the error but don't fail the verification
                    logger.warning(
                        f"Failed to send verification success email: {str(email_error)}"
                    )

                logger.info(
                    f"Email verification completed successfully for {user.email}"
                )

                return Response(
                    {
                        "message": "Your email has been verified successfully! You can now log in to your account.",
                        "data": {
                            "email": user.email,
                            "username": user.username,
                            "is_verified": user.is_verified,
                            "verified_at": user.updated_at,
                        },
                    },
                    status=status.HTTP_200_OK,
                )

            except KeyError as ke:
                logger.error(f"Missing key in validated data: {str(ke)}")
                return Response(
                    {"error": "Invalid verification data. Please try again."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            except Exception as e:
                logger.exception(
                    f"Unexpected error during email verification: {str(e)}"
                )
                return Response(
                    {
                        "error": "An unexpected error occurred during verification. Please try again later."
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Validation failed
        logger.warning(f"Email verification validation failed: {serializer.errors}")

        # Check for specific error types to return appropriate status codes
        errors = serializer.errors

        # If OTP expired
        if "otp" in errors and any(
            "expired" in str(err).lower() for err in errors["otp"]
        ):
            return Response(serializer.errors, status=status.HTTP_410_GONE)

        # If user not found
        if "email" in errors and any(
            "not found" in str(err).lower() for err in errors["email"]
        ):
            return Response(serializer.errors, status=status.HTTP_404_NOT_FOUND)

        # Default to bad request
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResendOTPView(APIView):
    """
    API View for Resending OTP.

    Handles requests to resend verification OTP to users who didn't receive
    the original code or whose code has expired.

    Rate Limiting Logic:
    - Checks if user has an active (unexpired and unused) OTP
    - If active OTP exists, returns error with remaining time
    - Only creates new OTP if old one is expired or used
    """

    permission_classes = [AllowAny]
    serializer_class = ResendOTPSerializer

    @extend_schema(
        summary="Resend verification OTP",
        description="Resend a verification OTP to the user's email address. A new OTP will only be sent if the previous one has expired or been used.",
        request=ResendOTPSerializer,
        responses={
            200: OpenApiResponse(description="OTP resent successfully"),
            400: OpenApiResponse(
                description="Bad Request - Invalid email or user already verified"
            ),
            404: OpenApiResponse(description="User not found"),
            429: OpenApiResponse(
                description="Too Many Requests - Active OTP still valid"
            ),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to resend OTP.

        Steps:
        1. Validate email address.
        2. Check if user exists and is not verified.
        3. Check if user has active (unexpired and unused) OTP.
        4. If active OTP exists, return error with remaining time.
        5. If OTP is expired or used, delete it and create new one.
        6. Send new OTP via email.
        7. Return success response.

        Args:
            request: HTTP request containing email.

        Returns:
            Response: Success or error message with appropriate status code.
        """
        logger.info("Received OTP resend request")

        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            try:
                email = serializer.validated_data["email"]
                logger.info(f"Processing OTP resend for email: {email}")

                # Get the user
                try:
                    user = User.objects.get(email=email)
                except User.DoesNotExist:
                    logger.error(f"User not found for email: {email}")
                    return Response(
                        {"error": "User not found."}, status=status.HTTP_404_NOT_FOUND
                    )

                # Check for existing OTP for this user and code_type
                from django.utils import timezone
                from auths.models import Passcode

                try:
                    existing_otp = Passcode.objects.get(
                        user=user,
                        code_type=choices.CodeType.VERIFICATION,
                        is_used=False,
                    )

                    # Check if OTP is still valid (not expired)
                    if existing_otp.expires_at > timezone.now():
                        # OTP is still active - don't create new one
                        remaining_time = existing_otp.expires_at - timezone.now()
                        total_seconds = int(remaining_time.total_seconds())
                        minutes_remaining = total_seconds // 60
                        seconds_remaining = total_seconds % 60

                        logger.warning(
                            f"Active OTP already exists for {email}. "
                            f"Expires in {minutes_remaining}m {seconds_remaining}s"
                        )

                        # Format time remaining message
                        if minutes_remaining > 0:
                            time_msg = f"{minutes_remaining} minute(s) and {seconds_remaining} second(s)"
                        else:
                            time_msg = f"{seconds_remaining} second(s)"

                        return Response(
                            {
                                "error": "An active verification code already exists.",
                                "message": f"Please use your existing verification code. It will expire in {time_msg}.",
                                "expires_in_seconds": total_seconds,
                                "expires_in_minutes": minutes_remaining,
                            },
                            status=status.HTTP_429_TOO_MANY_REQUESTS,
                        )
                    else:
                        # OTP exists but is expired - delete it
                        logger.info(f"Found expired OTP for {email}, deleting it")
                        existing_otp.delete()

                except Passcode.DoesNotExist:
                    # No existing OTP found, or it was used - proceed to create new one
                    logger.info(f"No active OTP found for {email}, will create new one")
                    pass

                # Create and send new OTP
                # Note: create_and_send_otp will delete any remaining OTPs (used ones)
                # and create a fresh one
                otp, error_msg, error_status = create_and_send_otp(
                    user=user,
                    code_type=choices.CodeType.VERIFICATION,
                    purpose="verification",
                )

                if error_msg:
                    logger.error(f"Failed to create/send OTP for {email}: {error_msg}")
                    return Response({"error": error_msg}, status=error_status)

                logger.info(f"OTP successfully resent to {email}")

                return Response(
                    {
                        "message": "A new verification code has been sent to your email.",
                        "data": {"email": email, "expires_in": "10 minutes"},
                    },
                    status=status.HTTP_200_OK,
                )

            except Exception as e:
                logger.exception(f"Unexpected error during OTP resend: {str(e)}")
                return Response(
                    {"error": "An unexpected error occurred. Please try again later."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Validation failed
        logger.warning(f"OTP resend validation failed: {serializer.errors}")

        # Check for specific error types
        errors = serializer.errors

        # If user not found
        if "email" in errors and any(
            "not found" in str(err).lower() for err in errors["email"]
        ):
            return Response(serializer.errors, status=status.HTTP_404_NOT_FOUND)

        # Default to bad request
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserProfileManageView(APIView):
    """
    API View for managing user profile.

    Handles both creation and update of the user profile.
    - If profile exists: Updates it (Partial update).
    - If profile does not exist: Creates it.
    """

    permission_classes = [IsActiveAndVerified]
    serializer_class = ProfileSerializer

    @extend_schema(
        summary="Create or Update user profile",
        description="Create a profile if it doesn't exist, or update the existing one (partial update).",
        request=ProfileSerializer,
        responses={
            200: OpenApiResponse(description="Profile updated successfully"),
            201: OpenApiResponse(description="Profile created successfully"),
            400: OpenApiResponse(description="Bad Request - Invalid data"),
            401: OpenApiResponse(description="Unauthorized"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to create or update user profile.

        Steps:
        1. Check if user has a profile.
        2. If exists: Update (partial).
        3. If not: Create new.

        Args:
            request: HTTP request containing profile data.

        Returns:
            Response: Profile data and status code (200 or 201).
        """
        logger.info(f"Profile manage request by user: {request.user.email}")

        try:
            # Check if profile exists
            if hasattr(request.user, "user_profile"):
                # Update existing profile
                profile = request.user.user_profile
                logger.info(f"Updating existing profile for user: {request.user.email}")

                serializer = self.serializer_class(
                    instance=profile, data=request.data, partial=True
                )

                if serializer.is_valid():
                    serializer.save()
                    logger.info(
                        f"Profile updated successfully for user: {request.user.email}"
                    )
                    return Response(serializer.data, status=status.HTTP_200_OK)
            else:
                # Create new profile
                logger.info(f"Creating new profile for user: {request.user.email}")

                serializer = self.serializer_class(data=request.data)

                if serializer.is_valid():
                    serializer.save(user=request.user)
                    logger.info(
                        f"Profile created successfully for user: {request.user.email}"
                    )
                    return Response(serializer.data, status=status.HTTP_201_CREATED)

            # If validation failed
            logger.warning(f"Profile validation failed: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.exception(
                f"Error managing profile for user {request.user.email}: {str(e)}"
            )
            return Response(
                {"error": "An unexpected error occurred. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PasswordChangeView(APIView):
    """
    API View for changing user password.

    Allows authenticated users to change their password by providing
    their current password and a new password.
    """

    permission_classes = [IsActiveAndVerified]
    serializer_class = PasswordChangeSerializer

    @extend_schema(
        summary="Change user password",
        description="Change the authenticated user's password. Requires current password verification.",
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(description="Password changed successfully"),
            400: OpenApiResponse(
                description="Bad Request - Invalid data or incorrect old password"
            ),
            401: OpenApiResponse(description="Unauthorized"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to change user password.

        Steps:
        1. Validate old password is correct.
        2. Validate new password meets complexity requirements.
        3. Validate new passwords match.
        4. Update user password.
        5. Return success response.

        Args:
            request: HTTP request containing password data.

        Returns:
            Response: Success message or error details.
        """
        logger.info(f"Password change requested by user: {request.user.email}")

        # Pass request context to serializer for old password validation
        serializer = self.serializer_class(
            data=request.data, context={"request": request}
        )

        if serializer.is_valid():
            try:
                # Extract validated data
                new_password = serializer.validated_data["new_password"]

                # Update user password
                request.user.set_password(new_password)
                request.user.save(update_fields=["password"])

                logger.info(
                    f"Password changed successfully for user: {request.user.email}"
                )

                return Response(
                    {
                        "message": "Password changed successfully.",
                        "data": {
                            "email": request.user.email,
                            "changed_at": request.user.updated_at,
                        },
                    },
                    status=status.HTTP_200_OK,
                )

            except Exception as e:
                logger.exception(
                    f"Error changing password for user {request.user.email}: {str(e)}"
                )
                return Response(
                    {
                        "error": "An unexpected error occurred while changing password. Please try again later."
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Validation failed
        logger.warning(
            f"Password change validation failed for {request.user.email}: {serializer.errors}"
        )

        # Check for specific error types
        errors = serializer.errors

        # If old password is incorrect
        if "old_password" in errors:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Default to bad request
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetRequestView(APIView):
    """
    API View for requesting password reset.

    Sends OTP to user's email for password reset verification.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer

    @extend_schema(
        summary="Request password reset",
        description="Request a password reset by providing email. An OTP will be sent to the email address.",
        request=PasswordResetRequestSerializer,
        responses={
            200: OpenApiResponse(description="Reset code sent successfully"),
            400: OpenApiResponse(description="Bad Request - Invalid email"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to initiate password reset.

        Steps:
        1. Validate email address.
        2. Check if user exists (silently for security).
        3. Check if user has active (unexpired and unused) password reset OTP.
        4. If active OTP exists, inform user (via email for security).
        5. If OTP is expired or used, delete it and create new one.
        6. Generate and send OTP.
        7. Return success response.

        Args:
            request: HTTP request containing email.

        Returns:
            Response: Success message (always, for security).
        """
        logger.info("Password reset requested")

        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            email = serializer.validated_data["email"]
            logger.info(f"Processing password reset request for: {email}")

            try:
                # Try to get user
                try:
                    user = User.objects.get(email=email)

                    # Check for existing password reset OTP
                    from django.utils import timezone
                    from auths.models import Passcode

                    try:
                        existing_otp = Passcode.objects.get(
                            user=user,
                            code_type=choices.CodeType.PASSWORD_RESET,
                            is_used=False,
                        )

                        # Check if OTP is still valid (not expired)
                        if existing_otp.expires_at > timezone.now():
                            # OTP is still active - resend the same code
                            remaining_time = existing_otp.expires_at - timezone.now()
                            total_seconds = int(remaining_time.total_seconds())
                            minutes_remaining = total_seconds // 60

                            logger.info(
                                f"Active password reset OTP exists for {email}. "
                                f"Resending same code. Expires in {minutes_remaining}m"
                            )

                            # Resend the existing OTP via email
                            try:
                                from utils.utils import (
                                    send_code_to_user,
                                    format_expiry_time,
                                )

                                expiry_text = format_expiry_time(
                                    existing_otp.expires_at
                                )

                                send_code_to_user(
                                    email=user.email,
                                    otp_code=existing_otp.code,
                                    purpose="password_reset",
                                    expiry_text=expiry_text,
                                )
                                logger.info(
                                    f"Existing password reset OTP resent to {email}"
                                )
                            except Exception as email_error:
                                logger.error(
                                    f"Failed to resend existing OTP: {str(email_error)}"
                                )
                                # Don't reveal error to user for security

                            # Return success (don't reveal that we resent existing code)
                            return Response(
                                {
                                    "message": "If an account exists with this email, a password reset code has been sent.",
                                    "data": {
                                        "email": email,
                                        "expires_in": "10 minutes",
                                    },
                                },
                                status=status.HTTP_200_OK,
                            )
                        else:
                            # OTP exists but is expired - delete it
                            logger.info(
                                f"Found expired password reset OTP for {email}, deleting it"
                            )
                            existing_otp.delete()

                    except Passcode.DoesNotExist:
                        # No existing OTP found, or it was used - proceed to create new one
                        logger.info(
                            f"No active password reset OTP for {email}, will create new one"
                        )
                        pass

                    # Create and send new OTP
                    # Note: create_and_send_otp will delete any remaining OTPs (used ones)
                    otp, error_msg, error_status = create_and_send_otp(
                        user=user,
                        code_type=choices.CodeType.PASSWORD_RESET,
                        purpose="password_reset",
                    )

                    if error_msg:
                        logger.error(
                            f"Failed to send password reset OTP to {email}: {error_msg}"
                        )
                        # Don't reveal error to user for security
                    else:
                        logger.info(f"New password reset OTP sent to {email}")

                except User.DoesNotExist:
                    logger.warning(
                        f"Password reset requested for non-existent email: {email}"
                    )
                    # Don't reveal that user doesn't exist (security)
                    pass

                # Always return success to prevent email enumeration
                return Response(
                    {
                        "message": "If an account exists with this email, a password reset code has been sent.",
                        "data": {"email": email, "expires_in": "10 minutes"},
                    },
                    status=status.HTTP_200_OK,
                )

            except Exception as e:
                logger.exception(f"Error processing password reset request: {str(e)}")
                return Response(
                    {"error": "An error occurred. Please try again later."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Validation failed
        logger.warning(f"Password reset request validation failed: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetVerifyView(APIView):
    """
    API View for verifying password reset OTP.

    Validates the OTP code before allowing password reset.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetVerifySerializer

    @extend_schema(
        summary="Verify password reset code",
        description="Verify the OTP sent to the user's email for password reset.",
        request=PasswordResetVerifySerializer,
        responses={
            200: OpenApiResponse(description="Code verified successfully"),
            400: OpenApiResponse(description="Bad Request - Invalid code or email"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to verify password reset OTP.

        Steps:
        1. Validate email and OTP.
        2. Verify OTP is valid, not expired, and not used.
        3. Return success response.

        Args:
            request: HTTP request containing email and otp.

        Returns:
            Response: Success message.
        """
        logger.info("Password reset verification requested")

        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            # If valid, it means OTP is correct and active
            # The serializer validation handles all checks
            email = serializer.validated_data["email"]
            logger.info(f"Password reset OTP verified successfully for: {email}")

            return Response(
                {
                    "message": "Verification code is valid.",
                    "data": {"email": email, "status": "verified"},
                },
                status=status.HTTP_200_OK,
            )

        # Validation failed
        logger.warning(f"Password reset verification failed: {serializer.errors}")
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetConfirmView(APIView):
    """
    API View for confirming password reset.

    Resets the user's password using the verified OTP and new password.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    @extend_schema(
        summary="Confirm password reset",
        description="Reset the user's password using the OTP and new password.",
        request=PasswordResetConfirmSerializer,
        responses={
            200: OpenApiResponse(description="Password reset successfully"),
            400: OpenApiResponse(description="Bad Request - Invalid data"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    def post(self, request):
        """
        Handle POST request to confirm password reset.

        Steps:
        1. Validate email, OTP, and new password.
        2. Verify OTP again (security).
        3. Update user password.
        4. Mark OTP as used.
        5. Return success response.

        Args:
            request: HTTP request containing email, otp, new_password.

        Returns:
            Response: Success message.
        """
        logger.info("Password reset confirmation requested")

        serializer = self.serializer_class(data=request.data)

        if serializer.is_valid():
            try:
                validated_data = serializer.validated_data
                user = validated_data["user"]
                passcode = validated_data["passcode"]
                new_password = validated_data["new_password"]

                logger.info(f"Processing password reset for user: {user.email}")

                # Use transaction
                from django.db import transaction

                try:
                    with transaction.atomic():
                        # Update password
                        user.set_password(new_password)
                        user.save(update_fields=["password"])
                        logger.info(f"Password updated for user {user.email}")

                        # Mark OTP as used
                        passcode.is_used = True
                        passcode.save(update_fields=["is_used"])
                        logger.info(
                            f"Password reset OTP marked as used for {user.email}"
                        )

                except Exception as db_error:
                    logger.exception(
                        f"Database error during password reset: {str(db_error)}"
                    )
                    return Response(
                        {"error": "Failed to reset password. Please try again."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                # Send confirmation email (optional)
                try:
                    from utils.utils import send_normal_email

                    email_data = {
                        "to_email": user.email,
                        "email_subject": "Password Reset Successful",
                        "email_body": (
                            f"Hi {user.first_name},\n\n"
                            f"Your password has been successfully reset.\n\n"
                            f"You can now log in with your new password.\n\n"
                            f"If you didn't perform this action, please contact our support team immediately.\n\n"
                            f"Best regards,\n"
                            f"AutoDocAI Team"
                        ),
                    }
                    send_normal_email(email_data)
                    logger.info(
                        f"Password reset confirmation email sent to {user.email}"
                    )
                except Exception as email_error:
                    logger.warning(
                        f"Failed to send password reset confirmation email: {str(email_error)}"
                    )

                return Response(
                    {
                        "message": "Your password has been reset successfully. You can now log in.",
                        "data": {"email": user.email, "updated_at": user.updated_at},
                    },
                    status=status.HTTP_200_OK,
                )

            except Exception as e:
                logger.exception(
                    f"Unexpected error during password reset confirmation: {str(e)}"
                )
                return Response(
                    {"error": "An unexpected error occurred. Please try again later."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        # Validation failed
        logger.warning(
            f"Password reset confirmation validation failed: {serializer.errors}"
        )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
