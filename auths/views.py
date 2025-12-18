from datetime import timedelta

from django.db import transaction, models
from django.utils import timezone
from django.contrib.auth import logout as django_logout
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import (
    RefreshToken,
    OutstandingToken,
    BlacklistedToken,
)

from drf_spectacular.utils import extend_schema, OpenApiResponse

from utils import choices, loggings
from utils.paginations import CustomPageNumberPagination
from utils.permissions import IsActiveAndVerified, IsAdminOnly
from utils.services.otp import create_and_send_otp

from .models import User, Passcode, DeviceSession, UserLoginAudit
from .serializers import (
    # PasswordChangeSerializer,
    # PasswordResetConfirmSerializer,
    # PasswordResetRequestSerializer,
    # PasswordResetVerifySerializer,
    # ProfileSerializer,
    # UserListSerializer,
    UserRegistrationSerializer,
    EmailVerificationSerializer,
    ResendOTPSerializer,
    LoginSerializer,
)

# Initialize logger
logger = loggings.setup_logging()


class UserRegistrationView(APIView):
    """
    Handle user registration and email verification initiation.

    Flow:
    1. Validate input
    2. Create user (atomic)
    3. Generate & send verification OTP
    """

    permission_classes = [AllowAny]
    serializer_class = UserRegistrationSerializer

    @extend_schema(
        summary="Register a new user",
        description="Create a user account and send an email verification code.",
        request=UserRegistrationSerializer,
        responses={
            201: OpenApiResponse(description="User registered successfully"),
            400: OpenApiResponse(description="Validation error"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request):
        logger.info("User registration request received")

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                user = serializer.save()

                otp, error_message, error_status = create_and_send_otp(
                    user=user,
                    code_type=choices.CodeType.VERIFICATION,
                    purpose="verification",
                )

                if error_message:
                    logger.error(
                        "OTP sending failed during registration",
                        extra={"user_id": user.id},
                    )
                    raise RuntimeError(error_message)

        except RuntimeError as exc:
            return Response(
                {"detail": str(exc)},
                status=error_status or status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        except Exception:
            logger.exception("Unexpected registration failure")
            return Response(
                {"detail": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info("Registration completed successfully", extra={"user_id": user.id})

        return Response(
            {
                "message": (
                    "Your account has been created successfully. "
                    "Please check your email for the verification code."
                )
            },
            status=status.HTTP_201_CREATED,
        )

class EmailVerificationView(APIView):
    """
    Verify a user's email address using an OTP.

    Responsibilities:
    - Orchestrate verification workflow
    - Apply atomic state changes
    - Return deterministic API responses
    """

    permission_classes = [AllowAny]
    serializer_class = EmailVerificationSerializer

    @extend_schema(
        summary="Verify email address",
        description="Verify a user's email using a one-time passcode (OTP).",
        request=EmailVerificationSerializer,
        responses={
            200: OpenApiResponse(description="Email verified successfully"),
            400: OpenApiResponse(description="Invalid email or OTP"),
            410: OpenApiResponse(description="OTP expired"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request):
        logger.info("Email verification request received")

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        passcode = serializer.validated_data["passcode"]

        try:
            with transaction.atomic():
                user.is_verified = True
                user.save(update_fields=["is_verified"])

                passcode.is_used = True
                passcode.save(update_fields=["is_used"])

        except Exception:
            logger.exception("Failed to finalize email verification")
            return Response(
                {"detail": "Failed to verify email. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "Email verification completed successfully",
            extra={"user_id": user.id, "email": user.email},
        )

        return Response(
            {
                "message": "Your email has been verified successfully.",
                "data": {
                    "email": user.email,
                    "is_verified": user.is_verified,
                },
            },
            status=status.HTTP_200_OK,
        )


class ResendOTPView(APIView):
    """
    Resend email verification OTP.

    Responsibilities:
    - Orchestrate resend flow
    - Delegate validation to serializer
    - Trigger OTP generation and async email delivery
    """

    permission_classes = [AllowAny]
    serializer_class = ResendOTPSerializer

    @extend_schema(
        summary="Resend verification OTP",
        description="Resend a verification OTP if no active code exists.",
        request=ResendOTPSerializer,
        responses={
            200: OpenApiResponse(description="OTP resent successfully"),
            400: OpenApiResponse(description="Invalid request"),
            429: OpenApiResponse(description="Active OTP still valid"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request):
        logger.info("OTP resend request received")

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]

        try:
            otp, error_message, error_status = create_and_send_otp(
                user=user,
                code_type=choices.CodeType.VERIFICATION,
                purpose="verification",
            )

            if error_message:
                logger.error(
                    "Failed to resend OTP",
                    extra={"user_id": user.id, "error": error_message},
                )
                return Response(
                    {"detail": error_message},
                    status=error_status or status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        except Exception:
            logger.exception("Unexpected error during OTP resend")
            return Response(
                {"detail": "An unexpected error occurred. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        logger.info(
            "OTP resent successfully",
            extra={"user_id": user.id, "email": user.email},
        )

        return Response(
            {
                "message": "A new verification code has been sent to your email.",
                "data": {
                    "email": user.email,
                    "expires_in_minutes": 10,
                },
            },
            status=status.HTTP_200_OK,
        )


class LoginView(APIView):
    """
    Authenticate a user and issue JWT tokens with:
    - Refresh token rotation
    - Device/session tracking
    - Login audit events
    - Brute-force protection
    """

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    @extend_schema(
        summary="User login (JWT)",
        description="Authenticate user credentials and return JWT tokens.",
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Login successful"),
            400: OpenApiResponse(description="Invalid credentials"),
            403: OpenApiResponse(description="Account inactive or unverified"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request):
        ip_address = request.META.get("REMOTE_ADDR")
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        logger.info("Login request received", extra={"ip": ip_address})

        serializer = self.serializer_class(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        email = serializer.validated_data["email"]

        try:
            # JWT Token Issuance (Expiration-controlled refresh)
            refresh = RefreshToken.for_user(user)
            refresh.set_exp(lifetime=timedelta(days=2))  # dynamic refresh expiration

            access_token = str(refresh.access_token)
            refresh_token = str(refresh)
            expires_in = refresh.access_token.lifetime.total_seconds()

            # Device / Session Tracking
            try:
                DeviceSession.objects.create(
                    user=user,
                    ip_address=ip_address,
                    device=user_agent[:255] or "Unknown Device",
                    last_activity=timezone.now(),
                )
            except Exception:
                # Device tracking must never block login
                logger.exception(
                    "Failed to create device session",
                    extra={"user_id": user.id},
                )

            # Login Audit (SUCCESS)
            UserLoginAudit.log_event(
                user=user,
                email=user.email,
                status=choices.LoginStatus.SUCCESS,
                ip_address=ip_address,
                device=user_agent,
                user_agent=user_agent,
            )

            logger.info(
                "User logged in successfully",
                extra={"user_id": user.id, "ip": ip_address},
            )

            return Response(
                {
                    "message": "Login successful.",
                    "tokens": {
                        "access": access_token,
                        "refresh": refresh_token,
                        "type": "Bearer",
                        "expires_in": expires_in,
                    },
                    "user": {
                        "id": str(user.id),
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "is_verified": user.is_verified,
                    },
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            # Unexpected Error Handling
            logger.exception(
                "Unexpected error during login",
                extra={"user_id": getattr(user, "id", None)},
            )

            UserLoginAudit.log_event(
                user=None,
                email=email,
                status=choices.LoginStatus.FAILURE,
                ip_address=request.META.get("REMOTE_ADDR"),
                device=request.META.get("HTTP_USER_AGENT", ""),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                failure_reason="Invalid credentials",
            )

            return Response(
                {"detail": "An unexpected error occurred. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class LogoutView(APIView):
    """
    Logs out the user completely by:
    - Blacklisting provided JWT refresh token, or all active tokens if none provided
    - Deleting DRF token (if any)
    - Clearing Django session
    - Deleting all DeviceSession records for the user
    - Deleting UserLoginAudit records for the user/email
    """

    permission_classes = [IsActiveAndVerified]
    serializer_class = None  # No input serializer required

    @transaction.atomic
    def post(self, request):
        user = request.user
        refresh_token = request.data.get("refresh_token")
        actions_performed = []
        warnings = []

        logger.info(f"Logout requested by user {user.id} ({user.email})")

        # JWT Blacklisting
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
                actions_performed.append("JWT refresh token blacklisted")
                logger.info(f"JWT refresh token blacklisted for user {user.id}")

            except TokenError as e:
                warning = f"JWT token blacklisting failed: {str(e)}"
                warnings.append(warning)
                logger.warning(f"{warning} for user {user.id}")

        else:
            # Blacklist all outstanding refresh tokens for this user
            try:
                tokens = OutstandingToken.objects.filter(user=user)
                count_blacklisted = 0
                for t in tokens:
                    try:
                        BlacklistedToken.objects.get_or_create(token=t)
                        count_blacklisted += 1

                    except Exception as e:
                        warnings.append(f"Failed to blacklist token {t.id}: {str(e)}")
                        logger.warning(
                            f"Failed to blacklist token {t.id} for user {user.id}: {str(e)}"
                        )

                actions_performed.append(
                    f"No refresh token provided: Blacklisted {count_blacklisted} active token(s)"
                )
                logger.info(
                    f"Blacklisted {count_blacklisted} outstanding tokens for user {user.id}"
                )

            except Exception as e:
                warning = f"Outstanding token blacklisting failed: {str(e)}"
                warnings.append(warning)
                logger.error(f"{warning} for user {user.id}")

        # DRF Token Deletion
        try:
            if hasattr(user, "auth_token"):
                user.auth_token.delete()
                actions_performed.append("DRF authentication token deleted")
                logger.info(f"DRF token deleted for user {user.id}")

            else:
                actions_performed.append("No DRF token found")

        except Exception as e:
            warning = f"DRF token deletion failed: {str(e)}"
            warnings.append(warning)
            logger.error(f"{warning} for user {user.id}")

        # Django Session Cleanup
        try:
            if hasattr(request, "session") and request.session.session_key:
                django_logout(request)
                actions_performed.append("Django session cleared")
                logger.info(f"Django session cleared for user {user.id}")

            else:
                actions_performed.append("No active session found")

        except Exception as e:
            warning = f"Django session clearing failed: {str(e)}"
            warnings.append(warning)
            logger.error(f"{warning} for user {user.id}")

        # DeviceSession Cleanup
        try:
            deleted_count, _ = user.sessions.all().delete()
            actions_performed.append(f"Deleted {deleted_count} DeviceSession(s)")
            logger.info(f"Deleted {deleted_count} DeviceSession(s) for user {user.id}")

        except Exception as e:
            warning = f"DeviceSession cleanup failed: {str(e)}"
            warnings.append(warning)
            logger.error(f"{warning} for user {user.id}")

        # UserLoginAudit Cleanup
        try:
            deleted_count, _ = UserLoginAudit.objects.filter(
                models.Q(user=user) | models.Q(email=user.email)
            ).delete()
            actions_performed.append(
                f"Deleted {deleted_count} UserLoginAudit record(s) (including failed attempts by email)"
            )
            logger.info(
                f"Deleted {deleted_count} UserLoginAudit record(s) for user {user.id} ({user.email})"
            )

        except Exception as e:
            warning = f"UserLoginAudit cleanup failed: {str(e)}"
            warnings.append(warning)
            logger.error(f"{warning} for user {user.id}")

        # Prepare Response
        response = {
            "success": True,
            "message": "Successfully logged out from all authentication methods.",
            "actions_performed": actions_performed,
        }
        if warnings:
            response["warnings"] = warnings
            logger.warning(
                f"Logout completed with warnings for user {user.id}: {warnings}"
            )

        return Response(response, status=status.HTTP_200_OK)


# class CustomTokenRefreshView(TokenRefreshView):
#     """
#     API View for Refreshing JWT Tokens.

#     Takes a valid refresh token and returns a new access token.
#     If 'ROTATE_REFRESH_TOKENS' is True in settings, also returns a new refresh token.
#     """

#     @extend_schema(
#         summary="Refresh JWT Access Token",
#         description="Get a new access token using a valid refresh token.",
#         responses={
#             200: OpenApiResponse(description="Token refreshed successfully"),
#             401: OpenApiResponse(description="Unauthorized - Invalid or expired token"),
#         },
#     )
#     def post(self, request, *args, **kwargs):
#         """
#         Handle POST request to refresh token.
#         """
#         logger.info("Token refresh requested")
#         try:
#             response = super().post(request, *args, **kwargs)
#             logger.info("Token refreshed successfully")
#             return response
#         except Exception as e:
#             logger.warning(f"Token refresh failed: {str(e)}")
#             raise e


# class UserListView(APIView):
#     """
#     API View for listing all users.

#     Only Super Admins and Superusers can access this view.
#     Returns paginated list of users with their profile information.
#     """

#     permission_classes = [IsAdminOnly]
#     serializer_class = UserListSerializer
#     pagination_class = CustomPageNumberPagination

#     @extend_schema(
#         summary="List all users",
#         description="Retrieve a paginated list of all users with their profile \
#         information. Only accessible by Super Admins and Superusers.",
#         responses={
#             200: UserListSerializer(many=True),
#             403: OpenApiResponse(description="Forbidden - Insufficient permissions"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def get(self, request):
#         """
#         Handle GET request to list all users.

#         Returns:
#             Paginated list of users with profile data.
#         """
#         logger.info(f"User list requested by: {request.user}")

#         try:
#             # Get all users and prefetch related profile data for optimization
#             # Use prefetch_related for reverse OneToOne relationship
#             queryset = (
#                 User.objects.prefetch_related("user_profile")
#                 .all()
#                 .order_by("-created_at")
#             )

#             # Apply pagination
#             paginator = self.pagination_class()
#             paginated_queryset = paginator.paginate_queryset(
#                 queryset, request, view=self
#             )

#             # Serialize the data
#             serializer = self.serializer_class(paginated_queryset, many=True)

#             logger.info(
#                 f"Successfully retrieved {len(serializer.data)} users for {request.user}"
#             )

#             # Return paginated response
#             return paginator.get_paginated_response(serializer.data)

#         except Exception as e:
#             logger.exception(f"Error retrieving user list: {str(e)}")
#             return Response(
#                 {"error": "An error occurred while retrieving users."},
#                 status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             )


# class UserProfileView(APIView):
#     """
#     API View for retrieving the current user's profile.

#     Returns the user's information along with their profile data.
#     If the profile does not exist, the profile field will be null.
#     """

#     permission_classes = [IsActiveAndVerified]
#     serializer_class = UserListSerializer

#     @extend_schema(
#         summary="Get user profile",
#         description="Retrieve the authenticated user's information and profile data.",
#         responses={
#             200: UserListSerializer,
#             401: OpenApiResponse(description="Unauthorized"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def get(self, request):
#         """
#         Handle GET request to retrieve user profile.

#         Returns:
#             User data with profile information.
#         """
#         logger.info(f"User profile requested by: {request.user}")

#         try:
#             # The user is already available in request.user
#             # We use the serializer to format the response
#             serializer = self.serializer_class(request.user)

#             logger.info(f"Successfully retrieved profile for {request.user}")
#             return Response(serializer.data, status=status.HTTP_200_OK)

#         except Exception as e:
#             logger.exception(f"Error retrieving user profile: {str(e)}")
#             return Response(
#                 {"error": "An error occurred while retrieving your profile."},
#                 status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             )


# class ResendOTPView(APIView):
#     """
#     API View for Resending OTP.

#     Handles requests to resend verification OTP to users who didn't receive
#     the original code or whose code has expired.

#     Rate Limiting Logic:
#     - Checks if user has an active (unexpired and unused) OTP
#     - If active OTP exists, returns error with remaining time
#     - Only creates new OTP if old one is expired or used
#     """

#     permission_classes = [AllowAny]
#     serializer_class = ResendOTPSerializer

#     @extend_schema(
#         summary="Resend verification OTP",
#         description="Resend a verification OTP to the user's email address.\n\n"
#         "A new OTP will only be sent if the previous one has expired or been used.",
#         request=ResendOTPSerializer,
#         responses={
#             200: OpenApiResponse(description="OTP resent successfully"),
#             400: OpenApiResponse(
#                 description="Bad Request - Invalid email or user already verified"
#             ),
#             404: OpenApiResponse(description="User not found"),
#             429: OpenApiResponse(
#                 description="Too Many Requests - Active OTP still valid"
#             ),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def post(self, request):
#         """
#         Handle POST request to resend OTP.

#         Steps:
#         1. Validate email address.
#         2. Check if user exists and is not verified.
#         3. Check if user has active (unexpired and unused) OTP.
#         4. If active OTP exists, return error with remaining time.
#         5. If OTP is expired or used, delete it and create new one.
#         6. Send new OTP via email.
#         7. Return success response.

#         Args:
#             request: HTTP request containing email.

#         Returns:
#             Response: Success or error message with appropriate status code.
#         """
#         logger.info("Received OTP resend request")

#         serializer = self.serializer_class(data=request.data)

#         if serializer.is_valid():
#             try:
#                 email = serializer.validated_data["email"]
#                 logger.info(f"Processing OTP resend for email: {email}")

#                 # Get the user
#                 try:
#                     user = User.objects.get(email=email)
#                 except User.DoesNotExist:
#                     logger.error(f"User not found for email: {email}")
#                     return Response(
#                         {"error": "User not found."}, status=status.HTTP_404_NOT_FOUND
#                     )

#                 # Check for existing OTP for this user and code_type
#                 from django.utils import timezone

#                 from auths.models import Passcode

#                 try:
#                     existing_otp = Passcode.objects.get(
#                         user=user,
#                         code_type=choices.CodeType.VERIFICATION,
#                         is_used=False,
#                     )

#                     # Check if OTP is still valid (not expired)
#                     if existing_otp.expires_at > timezone.now():
#                         # OTP is still active - don't create new one
#                         remaining_time = existing_otp.expires_at - timezone.now()
#                         total_seconds = int(remaining_time.total_seconds())
#                         minutes_remaining = total_seconds // 60
#                         seconds_remaining = total_seconds % 60

#                         logger.warning(
#                             f"Active OTP already exists for {email}. "
#                             f"Expires in {minutes_remaining}m {seconds_remaining}s"
#                         )

#                         # Format time remaining message
#                         if minutes_remaining > 0:
#                             time_msg = f"{minutes_remaining} minute(s) and {seconds_remaining} second(s)"
#                         else:
#                             time_msg = f"{seconds_remaining} second(s)"

#                         return Response(
#                             {
#                                 "error": "An active verification code already exists.",
#                                 "message": f"Please use your existing verification code. It will expire in {time_msg}.",
#                                 "expires_in_seconds": total_seconds,
#                                 "expires_in_minutes": minutes_remaining,
#                             },
#                             status=status.HTTP_429_TOO_MANY_REQUESTS,
#                         )
#                     else:
#                         # OTP exists but is expired - delete it
#                         logger.info(f"Found expired OTP for {email}, deleting it")
#                         existing_otp.delete()

#                 except Passcode.DoesNotExist:
#                     # No existing OTP found, or it was used - proceed to create new one
#                     logger.info(f"No active OTP found for {email}, will create new one")
#                     pass

#                 # Create and send new OTP
#                 # Note: create_and_send_otp will delete any remaining OTPs (used ones)
#                 # and create a fresh one
#                 otp, error_msg, error_status = create_and_send_otp(
#                     user=user,
#                     code_type=choices.CodeType.VERIFICATION,
#                     purpose="verification",
#                 )

#                 if error_msg:
#                     logger.error(f"Failed to create/send OTP for {email}: {error_msg}")
#                     return Response({"error": error_msg}, status=error_status)

#                 logger.info(f"OTP successfully resent to {email}")

#                 return Response(
#                     {
#                         "message": "A new verification code has been sent to your email.",
#                         "data": {"email": email, "expires_in": "10 minutes"},
#                     },
#                     status=status.HTTP_200_OK,
#                 )

#             except Exception as e:
#                 logger.exception(f"Unexpected error during OTP resend: {str(e)}")
#                 return Response(
#                     {"error": "An unexpected error occurred. Please try again later."},
#                     status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 )

#         # Validation failed
#         logger.warning(f"OTP resend validation failed: {serializer.errors}")

#         # Check for specific error types
#         errors = serializer.errors

#         # If user not found
#         if "email" in errors and any(
#             "not found" in str(err).lower() for err in errors["email"]
#         ):
#             return Response(serializer.errors, status=status.HTTP_404_NOT_FOUND)

#         # Default to bad request
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class UserProfileManageView(APIView):
#     """
#     API View for managing user profile.

#     Handles both creation and update of the user profile.
#     - If profile exists: Updates it (Partial update).
#     - If profile does not exist: Creates it.
#     """

#     permission_classes = [IsActiveAndVerified]
#     serializer_class = ProfileSerializer

#     @extend_schema(
#         summary="Create or Update user profile",
#         description="Create a profile if it doesn't exist, or update the existing one (partial update).",
#         request=ProfileSerializer,
#         responses={
#             200: OpenApiResponse(description="Profile updated successfully"),
#             201: OpenApiResponse(description="Profile created successfully"),
#             400: OpenApiResponse(description="Bad Request - Invalid data"),
#             401: OpenApiResponse(description="Unauthorized"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def post(self, request):
#         """
#         Handle POST request to create or update user profile.

#         Steps:
#         1. Check if user has a profile.
#         2. If exists: Update (partial).
#         3. If not: Create new.

#         Args:
#             request: HTTP request containing profile data.

#         Returns:
#             Response: Profile data and status code (200 or 201).
#         """
#         logger.info(f"Profile manage request by user: {request.user}")

#         try:
#             # Check if profile exists
#             if hasattr(request.user, "user_profile"):
#                 # Update existing profile
#                 profile = request.user.user_profile
#                 logger.info(f"Updating existing profile for user: {request.user}")

#                 serializer = self.serializer_class(
#                     instance=profile, data=request.data, partial=True
#                 )

#                 if serializer.is_valid():
#                     serializer.save()
#                     logger.info(
#                         f"Profile updated successfully for user: {request.user}"
#                     )
#                     return Response(serializer.data, status=status.HTTP_200_OK)
#             else:
#                 # Create new profile
#                 logger.info(f"Creating new profile for user: {request.user}")

#                 serializer = self.serializer_class(data=request.data)

#                 if serializer.is_valid():
#                     serializer.save(user=request.user)
#                     logger.info(
#                         f"Profile created successfully for user: {request.user}"
#                     )
#                     return Response(serializer.data, status=status.HTTP_201_CREATED)

#             # If validation failed
#             logger.warning(f"Profile validation failed: {serializer.errors}")
#             return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#         except Exception as e:
#             logger.exception(
#                 f"Error managing profile for user {request.user}: {str(e)}"
#             )
#             return Response(
#                 {"error": "An unexpected error occurred. Please try again later."},
#                 status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             )


# class PasswordChangeView(APIView):
#     """
#     API View for changing user password.

#     Allows authenticated users to change their password by providing
#     their current password and a new password.
#     """

#     permission_classes = [IsActiveAndVerified]
#     serializer_class = PasswordChangeSerializer

#     @extend_schema(
#         summary="Change user password",
#         description="Change the authenticated user's password. Requires current password verification.",
#         request=PasswordChangeSerializer,
#         responses={
#             200: OpenApiResponse(description="Password changed successfully"),
#             400: OpenApiResponse(
#                 description="Bad Request - Invalid data or incorrect old password"
#             ),
#             401: OpenApiResponse(description="Unauthorized"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def post(self, request):
#         """
#         Handle POST request to change user password.

#         Steps:
#         1. Validate old password is correct.
#         2. Validate new password meets complexity requirements.
#         3. Validate new passwords match.
#         4. Update user password.
#         5. Return success response.

#         Args:
#             request: HTTP request containing password data.

#         Returns:
#             Response: Success message or error details.
#         """
#         logger.info(f"Password change requested by user: {request.user}")

#         # Pass request context to serializer for old password validation
#         serializer = self.serializer_class(
#             data=request.data, context={"request": request}
#         )

#         if serializer.is_valid():
#             try:
#                 # Extract validated data
#                 new_password = serializer.validated_data["new_password"]

#                 # Update user password
#                 request.user.set_password(new_password)
#                 request.user.save(update_fields=["password"])

#                 logger.info(f"Password changed successfully for user: {request.user}")

#                 return Response(
#                     {
#                         "message": "Password changed successfully.",
#                         "data": {
#                             "email": request.user.email,
#                             "changed_at": request.user.updated_at,
#                         },
#                     },
#                     status=status.HTTP_200_OK,
#                 )

#             except Exception as e:
#                 logger.exception(
#                     f"Error changing password for user {request.user}: {str(e)}"
#                 )
#                 return Response(
#                     {
#                         "error": "An unexpected error occurred while changing password. Please try again later."
#                     },
#                     status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 )

#         # Validation failed
#         logger.warning(
#             f"Password change validation failed for {request.user}: {serializer.errors}"
#         )

#         # Check for specific error types
#         errors = serializer.errors

#         # If old password is incorrect
#         if "old_password" in errors:
#             return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#         # Default to bad request
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class PasswordResetRequestView(APIView):
#     """
#     API View for requesting password reset.

#     Sends OTP to user's email for password reset verification.
#     """

#     permission_classes = [AllowAny]
#     serializer_class = PasswordResetRequestSerializer

#     @extend_schema(
#         summary="Request password reset",
#         description="Request a password reset by providing email. An OTP will be sent to the email address.",
#         request=PasswordResetRequestSerializer,
#         responses={
#             200: OpenApiResponse(description="Reset code sent successfully"),
#             400: OpenApiResponse(description="Bad Request - Invalid email"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def post(self, request):
#         """
#         Handle POST request to initiate password reset.

#         Steps:
#         1. Validate email address.
#         2. Check if user exists (silently for security).
#         3. Check if user has active (unexpired and unused) password reset OTP.
#         4. If active OTP exists, inform user (via email for security).
#         5. If OTP is expired or used, delete it and create new one.
#         6. Generate and send OTP.
#         7. Return success response.

#         Args:
#             request: HTTP request containing email.

#         Returns:
#             Response: Success message (always, for security).
#         """
#         logger.info("Password reset requested")

#         serializer = self.serializer_class(data=request.data)

#         if serializer.is_valid():
#             email = serializer.validated_data["email"]
#             logger.info(f"Processing password reset request for: {email}")

#             try:
#                 # Try to get user
#                 try:
#                     user = User.objects.get(email=email)

#                     # Check for existing password reset OTP
#                     from django.utils import timezone

#                     from auths.models import Passcode

#                     try:
#                         existing_otp = Passcode.objects.get(
#                             user=user,
#                             code_type=choices.CodeType.PASSWORD_RESET,
#                             is_used=False,
#                         )

#                         # Check if OTP is still valid (not expired)
#                         if existing_otp.expires_at > timezone.now():
#                             # OTP is still active - resend the same code
#                             remaining_time = existing_otp.expires_at - timezone.now()
#                             total_seconds = int(remaining_time.total_seconds())
#                             minutes_remaining = total_seconds // 60

#                             logger.info(
#                                 f"Active password reset OTP exists for {email}. "
#                                 f"Resending same code. Expires in {minutes_remaining}m"
#                             )

#                             # Resend the existing OTP via email
#                             try:
#                                 from utils.utils import (
#                                     format_expiry_time,
#                                     send_code_to_user,
#                                 )

#                                 expiry_text = format_expiry_time(
#                                     existing_otp.expires_at
#                                 )

#                                 send_code_to_user(
#                                     email=user.email,
#                                     otp_code=existing_otp.code,
#                                     purpose="password_reset",
#                                     expiry_text=expiry_text,
#                                 )
#                                 logger.info(
#                                     f"Existing password reset OTP resent to {email}"
#                                 )
#                             except Exception as email_error:
#                                 logger.error(
#                                     f"Failed to resend existing OTP: {str(email_error)}"
#                                 )
#                                 # Don't reveal error to user for security

#                             # Return success (don't reveal that we resent existing code)
#                             return Response(
#                                 {
#                                     "message": "If an account exists with this email, a password reset code has been sent.",
#                                     "data": {
#                                         "email": email,
#                                         "expires_in": "10 minutes",
#                                     },
#                                 },
#                                 status=status.HTTP_200_OK,
#                             )
#                         else:
#                             # OTP exists but is expired - delete it
#                             logger.info(
#                                 f"Found expired password reset OTP for {email}, deleting it"
#                             )
#                             existing_otp.delete()

#                     except Passcode.DoesNotExist:
#                         # No existing OTP found, or it was used - proceed to create new one
#                         logger.info(
#                             f"No active password reset OTP for {email}, will create new one"
#                         )
#                         pass

#                     # Create and send new OTP
#                     # Note: create_and_send_otp will delete any remaining OTPs (used ones)
#                     otp, error_msg, error_status = create_and_send_otp(
#                         user=user,
#                         code_type=choices.CodeType.PASSWORD_RESET,
#                         purpose="password_reset",
#                     )

#                     if error_msg:
#                         logger.error(
#                             f"Failed to send password reset OTP to {email}: {error_msg}"
#                         )
#                         # Don't reveal error to user for security
#                     else:
#                         logger.info(f"New password reset OTP sent to {email}")

#                 except User.DoesNotExist:
#                     logger.warning(
#                         f"Password reset requested for non-existent email: {email}"
#                     )
#                     # Don't reveal that user doesn't exist (security)
#                     pass

#                 # Always return success to prevent email enumeration
#                 return Response(
#                     {
#                         "message": "If an account exists with this email, a password reset code has been sent.",
#                         "data": {"email": email, "expires_in": "10 minutes"},
#                     },
#                     status=status.HTTP_200_OK,
#                 )

#             except Exception as e:
#                 logger.exception(f"Error processing password reset request: {str(e)}")
#                 return Response(
#                     {"error": "An error occurred. Please try again later."},
#                     status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 )

#         # Validation failed
#         logger.warning(f"Password reset request validation failed: {serializer.errors}")
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class PasswordResetVerifyView(APIView):
#     """
#     API View for verifying password reset OTP.

#     Validates the OTP code before allowing password reset.
#     """

#     permission_classes = [AllowAny]
#     serializer_class = PasswordResetVerifySerializer

#     @extend_schema(
#         summary="Verify password reset code",
#         description="Verify the OTP sent to the user's email for password reset.",
#         request=PasswordResetVerifySerializer,
#         responses={
#             200: OpenApiResponse(description="Code verified successfully"),
#             400: OpenApiResponse(description="Bad Request - Invalid code or email"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def post(self, request):
#         """
#         Handle POST request to verify password reset OTP.

#         Steps:
#         1. Validate email and OTP.
#         2. Verify OTP is valid, not expired, and not used.
#         3. Return success response.

#         Args:
#             request: HTTP request containing email and otp.

#         Returns:
#             Response: Success message.
#         """
#         logger.info("Password reset verification requested")

#         serializer = self.serializer_class(data=request.data)

#         if serializer.is_valid():
#             # If valid, it means OTP is correct and active
#             # The serializer validation handles all checks
#             email = serializer.validated_data["email"]
#             logger.info(f"Password reset OTP verified successfully for: {email}")

#             return Response(
#                 {
#                     "message": "Verification code is valid.",
#                     "data": {"email": email, "status": "verified"},
#                 },
#                 status=status.HTTP_200_OK,
#             )

#         # Validation failed
#         logger.warning(f"Password reset verification failed: {serializer.errors}")
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class PasswordResetConfirmView(APIView):
#     """
#     API View for confirming password reset.

#     Resets the user's password using the verified OTP and new password.
#     """

#     permission_classes = [AllowAny]
#     serializer_class = PasswordResetConfirmSerializer

#     @extend_schema(
#         summary="Confirm password reset",
#         description="Reset the user's password using the OTP and new password.",
#         request=PasswordResetConfirmSerializer,
#         responses={
#             200: OpenApiResponse(description="Password reset successfully"),
#             400: OpenApiResponse(description="Bad Request - Invalid data"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     )
#     def post(self, request):
#         """
#         Handle POST request to confirm password reset.

#         Steps:
#         1. Validate email, OTP, and new password.
#         2. Verify OTP again (security).
#         3. Update user password.
#         4. Mark OTP as used.
#         5. Return success response.

#         Args:
#             request: HTTP request containing email, otp, new_password.

#         Returns:
#             Response: Success message.
#         """
#         logger.info("Password reset confirmation requested")

#         serializer = self.serializer_class(data=request.data)

#         if serializer.is_valid():
#             try:
#                 validated_data = serializer.validated_data
#                 user = validated_data["user"]
#                 passcode = validated_data["passcode"]
#                 new_password = validated_data["new_password"]

#                 logger.info(f"Processing password reset for user: {user.email}")

#                 # Use transaction
#                 from django.db import transaction

#                 try:
#                     with transaction.atomic():
#                         # Update password
#                         user.set_password(new_password)
#                         user.save(update_fields=["password"])
#                         logger.info(f"Password updated for user {user.email}")

#                         # Mark OTP as used
#                         passcode.is_used = True
#                         passcode.save(update_fields=["is_used"])
#                         logger.info(
#                             f"Password reset OTP marked as used for {user.email}"
#                         )

#                 except Exception as db_error:
#                     logger.exception(
#                         f"Database error during password reset: {str(db_error)}"
#                     )
#                     return Response(
#                         {"error": "Failed to reset password. Please try again."},
#                         status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                     )

#                 # Send confirmation email (optional)
#                 try:
#                     from utils.utils import send_normal_email

#                     email_data = {
#                         "to_email": user.email,
#                         "email_subject": "Password Reset Successful",
#                         "email_body": (
#                             f"Hi {user.first_name},\n\n"
#                             f"Your password has been successfully reset.\n\n"
#                             f"You can now log in with your new password.\n\n"
#                             f"If you didn't perform this action, please contact our support team immediately.\n\n"
#                             f"Best regards,\n"
#                             f"Fiance-API Team"
#                         ),
#                     }
#                     send_normal_email(email_data)
#                     logger.info(
#                         f"Password reset confirmation email sent to {user.email}"
#                     )
#                 except Exception as email_error:
#                     logger.warning(
#                         f"Failed to send password reset confirmation email: {str(email_error)}"
#                     )

#                 return Response(
#                     {
#                         "message": "Your password has been reset successfully. You can now log in.",
#                         "data": {"email": user.email, "updated_at": user.updated_at},
#                     },
#                     status=status.HTTP_200_OK,
#                 )

#             except Exception as e:
#                 logger.exception(
#                     f"Unexpected error during password reset confirmation: {str(e)}"
#                 )
#                 return Response(
#                     {"error": "An unexpected error occurred. Please try again later."},
#                     status=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 )

#         # Validation failed
#         logger.warning(
#             f"Password reset confirmation validation failed: {serializer.errors}"
#         )
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
