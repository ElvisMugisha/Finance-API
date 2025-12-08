from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .serializers import UserRegistrationSerializer
from .models import User
from utils import loggings, choices
from utils.utils import create_and_send_otp

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
