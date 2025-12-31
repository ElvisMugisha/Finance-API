import datetime
import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.db import models, transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import (
    BlacklistedToken,
    OutstandingToken,
    RefreshToken,
)
from rest_framework_simplejwt.views import TokenRefreshView

from utils import choices, exporters, filters, loggings
from utils.paginations import CustomPageNumberPagination
from utils.permissions import IsActiveAndVerified, IsStaffOrAdmin
from utils.services.otp import create_and_send_otp

from .models import DeviceSession, Passcode, Profile, User, UserLoginAudit
from .schema_examples import profile
from .serializers import (
    EmailVerificationSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetRequestSerializer,
    PasswordResetSetNewSerializer,
    PasswordResetVerifySerializer,
    ProfileSerializer,
    ResendOTPSerializer,
    UserListSerializer,
    UserMeSerializer,
    UserRegistrationSerializer,
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
    Resend verification OTP.

    Guarantees:
    - Only one active OTP per user
    - Rate-limited by OTP expiration
    - Safe retry behavior
    """

    permission_classes = [AllowAny]
    serializer_class = ResendOTPSerializer

    @extend_schema(
        summary="Resend verification OTP",
        description=(
            "Resend verification OTP to user's email.\n\n"
            "Rules:\n"
            "- Verified users are blocked\n"
            "- Active OTPs must expire before resending\n"
            "- Expired OTPs are cleaned automatically"
        ),
        request=ResendOTPSerializer,
        responses={
            200: OpenApiResponse(description="OTP resent successfully"),
            400: OpenApiResponse(description="Invalid request"),
            404: OpenApiResponse(description="User not found"),
            429: OpenApiResponse(description="Active OTP still valid"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request):
        logger.info("OTP resend request received")

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user: User = serializer.context["user"]
        now = timezone.now()

        try:
            # Fetch latest unused OTP
            existing_otp = (
                Passcode.objects.filter(
                    user=user,
                    code_type=choices.CodeType.VERIFICATION,
                    is_used=False,
                )
                .order_by("-expires_at")
                .first()
            )

            if existing_otp:
                if existing_otp.expires_at > now:
                    remaining_seconds = int(
                        (existing_otp.expires_at - now).total_seconds()
                    )

                    logger.warning(
                        "Resend OTP blocked: active OTP exists",
                        extra={
                            "user_id": user.id,
                            "remaining_seconds": remaining_seconds,
                        },
                    )

                    return Response(
                        {
                            "error": "An active verification code already exists.",
                            "expires_in_seconds": remaining_seconds,
                        },
                        status=status.HTTP_429_TOO_MANY_REQUESTS,
                    )

                # Expired OTP → cleanup
                logger.info(
                    "Expired OTP found, deleting",
                    extra={"user_id": user.id, "otp_id": existing_otp.id},
                )
                existing_otp.delete()

            # Create & send new OTP
            otp, error_msg, error_status = create_and_send_otp(
                user=user,
                code_type=choices.CodeType.VERIFICATION,
                purpose="verification",
            )

            if error_msg:
                logger.error(
                    "OTP resend failed",
                    extra={"user_id": user.id, "error": error_msg},
                )
                return Response({"error": error_msg}, status=error_status)

            logger.info("OTP resent successfully", extra={"user_id": user.id})
            return Response(
                {
                    "message": "A new verification code has been sent to your email.",
                    "email": user.email,
                },
                status=status.HTTP_200_OK,
            )

        except Exception:
            logger.exception(
                "Unexpected error during OTP resend",
                extra={"user_id": user.id},
            )
            return Response(
                {"error": "An unexpected error occurred. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
                device_str = user_agent[:255] or "Unknown Device"

                # Check for existing session with same user, ip, and device
                session, created = DeviceSession.objects.get_or_create(
                    user=user,
                    ip_address=ip_address,
                    device=device_str,
                    defaults={
                        "last_activity": timezone.now(),
                        "user_agent": user_agent,
                    },
                )

                if not created:
                    # Update last_activity if session already exists
                    session.last_activity = timezone.now()
                    session.user_agent = (
                        user_agent  # optionally update user_agent if changed
                    )
                    session.save(update_fields=["last_activity", "user_agent"])
                    logger.debug(
                        f"Updated last_activity for existing DeviceSession {session.id} for user {user.id}"
                    )
                else:
                    logger.debug(
                        f"Created new DeviceSession {session.id} for user {user.id}"
                    )

            except Exception:
                # Device tracking must never block login
                logger.exception(
                    "Failed to create/update device session",
                    extra={"user_id": user.id},
                )

            # Login Audit (SUCCESS)
            try:
                if user:
                    # Check for existing successful login audit
                    audit, created = UserLoginAudit.objects.get_or_create(
                        user=user,
                        ip_address=ip_address,
                        device=device_str[:255],
                        status=choices.LoginStatus.SUCCESS,
                        defaults={
                            "user_agent": user_agent,
                            "timestamp": timezone.now(),
                        },
                    )

                    if not created:
                        # Update timestamp if already exists
                        audit.timestamp = timezone.now()
                        audit.user_agent = user_agent
                        audit.save(update_fields=["timestamp", "user_agent"])
                        logger.debug(
                            f"Updated timestamp for existing successful UserLoginAudit {audit.id} for user {user.id}"
                        )
                    else:
                        logger.debug(
                            f"Created new successful UserLoginAudit {audit.id} for user {user.id}"
                        )

            except Exception:
                # Login audit must never block login
                logger.exception(
                    "Failed to create/update UserLoginAudit",
                    extra={"user_id": user.id},
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

        # JWT Blacklisting and Deletion
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
                actions_performed.append("JWT refresh token blacklisted")
                logger.info(f"JWT refresh token blacklisted for user {user.id}")

                # Delete the token from OutstandingToken
                OutstandingToken.objects.filter(token=token).delete()
                actions_performed.append(
                    "Provided JWT refresh token deleted from OutstandingToken"
                )
                logger.info(f"Provided JWT refresh token deleted for user {user.id}")

            except TokenError as e:
                warning = f"JWT token blacklisting failed: {str(e)}"
                warnings.append(warning)
                logger.warning(f"{warning} for user {user.id}")

        else:
            # Blacklist and delete all outstanding refresh tokens for this user
            try:
                tokens = OutstandingToken.objects.filter(user=user)
                count_blacklisted = 0
                count_deleted = 0

                for t in tokens:
                    try:
                        # Blacklist
                        BlacklistedToken.objects.get_or_create(token=t)
                        count_blacklisted += 1

                        # Delete token
                        t.delete()
                        count_deleted += 1

                    except Exception as e:
                        warnings.append(
                            f"Failed to blacklist/delete token {t.id}: {str(e)}"
                        )
                        logger.warning(
                            f"Failed to blacklist/delete token {t.id} for user {user.id}: {str(e)}"
                        )

                actions_performed.append(
                    f"Blacklisted {count_blacklisted} token(s) and deleted {count_deleted} token(s)"
                )
                logger.info(
                    f"Blacklisted {count_blacklisted} and deleted {count_deleted} outstanding tokens for user {user.id}"
                )

            except Exception as e:
                warning = f"Outstanding token blacklisting/deletion failed: {str(e)}"
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

        # UserLoginAudit Cleanup - delete only failed attempts
        try:
            failed_audits = UserLoginAudit.objects.filter(
                (models.Q(user=user) | models.Q(email=user.email))
                & models.Q(status=choices.LoginStatus.FAILURE)
            )
            deleted_count = failed_audits.count()
            failed_audits.delete()

            actions_performed.append(
                f"Deleted {deleted_count} failed UserLoginAudit record(s)"
            )
            logger.info(
                f"Deleted {deleted_count} failed UserLoginAudit record(s) for user {user.id} ({user.email})"
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


class PasswordChangeView(APIView):
    """
    API endpoint for changing the authenticated user's password.

    Access:
    - Authenticated users only
    - User must be active and verified

    Flow:
    1. Validate current password
    2. Validate new password rules
    3. Update password securely
    4. Return success response
    """

    permission_classes = [IsActiveAndVerified]
    serializer_class = PasswordChangeSerializer

    @extend_schema(
        summary="Change user password",
        description=(
            "Allows an authenticated, active, and verified user to change their password. "
            "The current password must be provided for verification."
        ),
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(description="Password changed successfully"),
            400: OpenApiResponse(
                description="Invalid input or password validation failed"
            ),
            401: OpenApiResponse(description="Unauthorized"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def post(self, request):
        user = request.user
        logger.info(
            "Password change requested",
            extra={"user_id": user.id, "email": user.email},
        )

        serializer = self.serializer_class(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            logger.warning(
                "Password change validation failed",
                extra={
                    "user_id": user.id,
                    "errors": serializer.errors,
                },
            )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            user.set_password(serializer.validated_data["new_password"])
            user.save(update_fields=["password"])

            logger.info(
                "Password changed successfully",
                extra={"user_id": user.id, "email": user.email},
            )

            return Response(
                {
                    "message": "Password changed successfully.",
                    "data": {
                        "email": user.email,
                        "changed_at": timezone.now(),
                    },
                },
                status=status.HTTP_200_OK,
            )

        except Exception as exc:
            logger.exception(
                "Unexpected error during password change",
                extra={"user_id": user.id},
            )
            return Response(
                {
                    "error": (
                        "An unexpected error occurred while changing your password. "
                        "Please try again later."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PasswordResetRequestView(APIView):
    """
    Initiates the password reset process.

    Always returns a success response to prevent email enumeration.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer

    @extend_schema(
        summary="Request password reset",
        request=PasswordResetRequestSerializer,
        responses={
            200: OpenApiResponse(description="Reset code sent if account exists")
        },
    )
    def post(self, request):
        logger.info("Password reset request received")

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        logger.info(f"Processing password reset request for: {email}")

        try:
            user = User.objects.filter(email=email).first()
            if not user:
                logger.warning(
                    "Password reset requested for non-existent email",
                    extra={"email": email},
                )
                return self._success_response(email)

            # Check for existing password reset OTP
            try:
                existing_otp = Passcode.objects.get(
                    user=user,
                    code_type=choices.CodeType.PASSWORD_RESET,
                    is_used=False,
                )

                # Check if OTP is still valid (not expired)
                if existing_otp.expires_at > timezone.now():
                    # OTP is still valid - remind user to use it
                    logger.info(
                        "Password reset OTP is still valid",
                        extra={"user_id": user.id, "email": email},
                    )
                    return self._success_response(email)

                else:
                    # OTP exists but is expired - delete it
                    logger.info(
                        "Password reset OTP is expired",
                        extra={"user_id": user.id, "email": email},
                    )
                    existing_otp.delete()

            except Passcode.DoesNotExist:
                # No existing OTP - create and send new one
                logger.info(
                    "No existing password reset OTP found",
                    extra={"user_id": user.id, "email": email},
                )
                pass

            # Create and send new OTP
            # Note: create_and_send_otp will delete any remaining OTPs (used ones)
            create_and_send_otp(
                user=user,
                code_type=choices.CodeType.PASSWORD_RESET,
                purpose="password_reset",
            )

            logger.info(
                "Password reset OTP sent",
                extra={"user_id": user.id, "email": email},
            )

        except Exception:
            logger.exception("Password reset request failed")
            # Intentionally silent for security

        return self._success_response(email)

    @staticmethod
    def _success_response(email: str) -> Response:
        return Response(
            {
                "message": (
                    "If an account exists with this email, "
                    "a password reset code has been sent."
                ),
                "data": {"email": email},
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetVerifyView(APIView):
    """
    Verifies a password reset OTP.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetVerifySerializer

    @extend_schema(
        summary="Verify password reset code",
        request=PasswordResetVerifySerializer,
        responses={200: OpenApiResponse(description="Code verified")},
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["user"].email
        logger.info("Password reset OTP verified", extra={"email": email})

        return Response(
            {
                "message": "Verification code is valid.",
                "data": {"email": email},
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetSetNewView(APIView):
    """
    Resets the user's password after OTP verification.
    """

    permission_classes = [AllowAny]
    serializer_class = PasswordResetSetNewSerializer

    @extend_schema(
        summary="Set new password",
        request=PasswordResetSetNewSerializer,
        responses={200: OpenApiResponse(description="Password reset successful")},
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        passcode = serializer.validated_data["passcode"]
        new_password = serializer.validated_data["new_password"]

        logger.info("Resetting password", extra={"email": user.email})

        try:
            with transaction.atomic():
                user.set_password(new_password)
                user.save(update_fields=["password"])

                passcode.is_used = True
                passcode.save(update_fields=["is_used"])

        except Exception:
            logger.exception("Password reset failed")
            return Response(
                {"error": "Failed to reset password. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": "Your password has been reset successfully.",
                "data": {"email": user.email},
            },
            status=status.HTTP_200_OK,
        )


class TokenRefreshAPIView(TokenRefreshView):
    """
    Refresh JWT access tokens.

    Delegates token validation and rotation entirely to SimpleJWT.
    Adds structured logging and preserves default error semantics.

    Design Principles:
    - KISS: No custom token logic
    - DRY: No duplicated JWT handling
    - SoC: Authentication logic remains in SimpleJWT
    - Security-first: No token data logged
    """

    @extend_schema(
        summary="Refresh JWT access token",
        description=(
            "Obtain a new access token using a valid refresh token. "
            "If refresh rotation is enabled, a new refresh token is also issued."
        ),
        responses={
            200: OpenApiResponse(description="Token refreshed successfully"),
            401: OpenApiResponse(description="Invalid or expired refresh token"),
        },
    )
    def post(self, request, *args, **kwargs):
        """
        Handle refresh token requests.

        This method intentionally avoids custom token validation logic
        and relies on SimpleJWT for correctness and security.
        """
        client_ip = request.META.get("REMOTE_ADDR")
        logger.info(
            "Token refresh requested",
            extra={"ip": client_ip},
        )

        try:
            response = super().post(request, *args, **kwargs)

            logger.info(
                "Token refreshed successfully",
                extra={"ip": client_ip},
            )

            return response

        except (InvalidToken, TokenError) as exc:
            # Expected authentication failure
            logger.warning(
                "Token refresh failed: invalid or expired token",
                extra={
                    "ip": client_ip,
                    "error": exc.__class__.__name__,
                },
            )
            raise

        except Exception:
            # Unexpected failure (misconfiguration, runtime error, etc.)
            logger.exception(
                "Unexpected error during token refresh",
                extra={"ip": client_ip},
            )
            return Response(
                {"detail": "Unable to refresh token at this time."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@extend_schema(
    parameters=[
        # Basic filters
        OpenApiParameter(
            "is_active", str, description="Filter by active status (true/false)"
        ),
        OpenApiParameter(
            "is_staff", str, description="Filter by staff status (true/false)"
        ),
        OpenApiParameter(
            "is_superuser", str, description="Filter by superuser status (true/false)"
        ),
        OpenApiParameter(
            "is_verified", str, description="Filter by verified status (true/false)"
        ),
        OpenApiParameter(
            "is_premium", str, description="Filter by premium status (true/false)"
        ),
        # Search
        OpenApiParameter(
            "search", str, description="Search by username, email, or name"
        ),
        # Ordering
        OpenApiParameter(
            "ordering",
            str,
            description="Order by fields (e.g., -created_at, last_login, email, -first_name, last_name)",
        ),
        # Export
        OpenApiParameter(
            "export",
            str,
            description="Export format, use 'excel' to generate Excel file",
        ),
    ],
    responses={
        200: UserListSerializer(many=True),
        403: OpenApiResponse(description="Forbidden – insufficient permissions"),
        500: OpenApiResponse(description="Internal server error"),
    },
)
class UserListView(generics.ListAPIView):
    """
    List all users with advanced filtering, search, ordering, and optional CSV export.

    Access restricted to staff/admin users.

    Excel Export:
    - When `export=excel` query param is set, a Excel file is saved to `media/excel/`
      with all filtered user data, including flattened profile fields.
    - Paginated JSON response is returned regardless.
    """

    queryset = User.objects.select_related("profile").all()
    serializer_class = UserListSerializer
    permission_classes = [IsStaffOrAdmin]
    pagination_class = CustomPageNumberPagination

    @extend_schema(
        summary="List all users",
        description=(
            "Retrieve a paginated list of users. Supports advanced filtering, "
            "search, ordering, and Excel export. Restricted to staff/admin users."
        ),
        responses={
            200: UserListSerializer(many=True),
            403: OpenApiResponse(description="Forbidden – insufficient permissions"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    def list(self, request, *args, **kwargs):
        logger.info("User list requested", extra={"requested_by": request.user.id})

        try:
            # Apply filters/search/ordering via django-filter
            user_filter = filters.UserFilter(
                request.query_params, queryset=self.queryset
            )
            filtered_queryset = user_filter.qs

            # Handle Excel export: save file locally
            excel_url = None
            if request.query_params.get("export") == "excel":
                # Serialize and flatten data
                serializer = self.get_serializer(filtered_queryset, many=True)
                flattened_data = exporters.flatten_user_data(serializer.data)

                # Save Excel locally
                excel_dir = os.path.join(settings.MEDIA_ROOT, "exports")
                os.makedirs(excel_dir, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                excel_filename = f"users_export_{timestamp}.xlsx"
                excel_path = os.path.join(excel_dir, excel_filename)

                exporters.export_to_excel(flattened_data, file_path=excel_path)

                excel_url = request.build_absolute_uri(
                    settings.MEDIA_URL + f"exports/users_export_{timestamp}.xlsx"
                )
                logger.info(
                    f"Excel export completed",
                    extra={
                        "requested_by": request.user.id,
                        "record_count": len(flattened_data),
                        "excel_url": excel_url,
                    },
                )

            # Paginate JSON response
            page = self.paginate_queryset(filtered_queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                response_data = self.get_paginated_response(serializer.data).data

                if excel_url:
                    response_data["excel_url"] = excel_url

                logger.info(
                    "User list retrieved successfully",
                    extra={
                        "requested_by": request.user.id,
                        "returned_count": len(serializer.data),
                    },
                )
                return Response(response_data, status=status.HTTP_200_OK)

            # Fallback: no pagination
            serializer = self.get_serializer(filtered_queryset, many=True)
            response_data = serializer.data
            if excel_url:
                response_data = {"results": response_data, "excel_url": excel_url}
            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(
                "Failed to retrieve user list",
                extra={"requested_by": request.user.id, "error": str(e)},
            )
            return Response(
                {"detail": "Unable to retrieve users at this time."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserProfileManageView(generics.GenericAPIView):
    """
    Create or update the authenticated user's profile.

    Behavior:
    - If profile exists → partial update
    - If profile does not exist → create
    """

    serializer_class = ProfileSerializer
    permission_classes = [IsActiveAndVerified]

    @extend_schema(
        summary="Create or update profile",
        description=(
            "Create a profile if it does not exist, "
            "or partially update the existing profile."
        ),
        request=ProfileSerializer,
        responses={
            200: ProfileSerializer,
            400: OpenApiResponse(
                description="Validation error",
                examples=[profile.PROFILE_VALIDATION_ERROR_EXAMPLE],
            ),
        },
        examples=[
            profile.PROFILE_REQUEST_EXAMPLE,
            profile.PROFILE_RESPONSE_EXAMPLE,
        ],
    )
    def patch(self, request):
        user = request.user

        logger.info(
            "Profile upsert requested",
            extra={"user_id": user.id},
        )

        try:
            profile, created = Profile.objects.get_or_create(user=user)

            serializer = self.get_serializer(
                profile,
                data=request.data,
                partial=True,
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            logger.info(
                "Profile upsert successful",
                extra={
                    "user_id": user.id,
                    "profile_was_created": created,
                },
            )

            return Response(serializer.data, status=status.HTTP_200_OK)

        except serializers.ValidationError:
            logger.warning(
                "Profile validation failed",
                extra={"user_id": user.id},
            )
            raise

        except Exception as exc:
            logger.exception(
                "Unexpected error during profile upsert",
                extra={"user_id": user.id},
            )
            return Response(
                {"detail": "Unable to update profile at this time."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserMeView(generics.RetrieveAPIView):
    """
    Retrieve the authenticated user's account and profile data.
    """

    serializer_class = UserMeSerializer
    permission_classes = [IsActiveAndVerified]

    @extend_schema(
        summary="Get logged-in user profile",
        description="Retrieve the authenticated user's account and profile details.",
        responses={200: UserMeSerializer},
        examples=[profile.USER_PROFILE_RESPONSE_EXAMPLE],
    )
    def get_object(self):
        logger.info(
            "User profile retrieved",
            extra={"user_id": self.request.user.id},
        )
        return self.request.user
