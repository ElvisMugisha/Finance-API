import random
import re
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response

from auths.models import User, Passcode
from utils import loggings, choices

# Initialize logger
logger = loggings.setup_logging()


def generate_otp():
    """
    Generate an 8-digit numeric one-time passcode (OTP).
    """
    logger.info("Generating an 8-digit OTP")
    otp = "".join(str(random.randint(0, 9)) for _ in range(8))
    logger.debug(f"Generated OTP: {otp}")
    return otp


def create_otp_for_user(user, code_type):
    """
    Create and store an OTP in the database for the given user and code type.

    This function ensures that only ONE OTP exists per user per code_type by:
    1. Deleting ALL existing OTPs (used or unused) for the same user and code_type
    2. Creating a new OTP with is_used=False

    Args:
        user: User instance for whom to create the OTP
        code_type: Type of code (VERIFICATION, PASSWORD_RESET, etc.)

    Returns:
        Passcode: The newly created OTP object with is_used=False

    Raises:
        ValueError: If user is None or code_type is invalid
        Exception: If OTP creation fails
    """
    logger.info(
        f"Creating OTP for user: {getattr(user, 'username', None)} with code_type: {code_type}"
    )
    valid_code_types = [choice[0] for choice in choices.CodeType.choices]

    if not user:
        logger.error("User instance is missing during OTP creation")
        raise ValueError("User is required for OTP creation")

    if code_type not in valid_code_types:
        logger.error(f"Invalid code_type provided: {code_type}")
        raise ValueError(f"Invalid code_type. Must be one of {valid_code_types}")

    try:
        # Generate a new OTP and its expiration timestamp
        otp_code = generate_otp()
        expires_at = timezone.now() + timedelta(minutes=10)

        # Delete ALL existing OTPs for the same user and code_type (both used and unused)
        # This ensures only ONE OTP exists per user per code_type at any time
        deleted_count = Passcode.objects.filter(
            user=user, code_type=code_type
        ).delete()[0]

        if deleted_count > 0:
            logger.info(
                f"Deleted {deleted_count} existing OTP(s) for user {user.email} "
                f"with code_type {code_type}"
            )
        else:
            logger.debug(
                f"No existing OTPs found for user {user.email} with code_type {code_type}"
            )

        # Create and save new OTP with is_used=False (default)
        otp = Passcode.objects.create(
            user=user,
            code=otp_code,
            code_type=code_type,
            expires_at=expires_at,
            is_used=False,  # Explicitly set to False (though it's the default)
        )
        logger.info(
            f"Created new OTP for user {user.email}, expires at {expires_at}. "
            f"is_used={otp.is_used}"
        )
        return otp

    except Exception as e:
        logger.exception(f"Failed to create OTP for user {user.email}: {str(e)}")
        raise


def send_code_to_user(email, otp_code, purpose="verification", expiry_text=None):
    """
    Send an OTP to the user's email based on the given purpose.
    """
    logger.info(f"Sending OTP email to {email} for purpose: {purpose}")
    try:
        user = get_user_by_email(email)
        if not user:
            return Response(
                {
                    "errors": {"email": _("User not found.")},
                    "message": _("Invalid email address."),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if purpose == "password_reset":
            subject = "One-Time Passcode for Password Reset"
            body = (
                f"Dear {user.first_name},\n\n"
                f"Use the following passcode to reset your password:\n\n"
                f"OTP: {otp_code}\n\n"
                f"This passcode is valid for {expiry_text or 'a few minutes'}.\n\n"
                f"If you didn't request this, please contact our support team.\n\n"
                f"Best,\nAutoDocAI Team"
            )

        elif purpose == "verification":
            subject = "One-Time Passcode for Email Verification"
            body = (
                f"Hi {user.first_name},\n\n"
                f"Use the following one-time passcode to verify your email:\n\n"
                f"OTP: {otp_code}\n\n"
                f"This passcode is valid for {expiry_text or 'a few minutes'}.\n\n"
                f"If this wasn't you, you can safely ignore this message.\n\n"
                f"Best,\nAutoDocAI Team"
            )

        else:
            logger.error(f"Invalid email purpose provided: {purpose}")
            raise ValueError("Invalid email purpose.")

        email_message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.EMAIL_FROM,
            to=[email],
        )
        email_message.send(fail_silently=False)
        logger.info(f"OTP email sent to {email} for purpose: {purpose}")

    except ObjectDoesNotExist:
        logger.error(f"No user found with email: {email}")
        raise

    except Exception as e:
        logger.exception(f"Error sending OTP email to {email}: {str(e)}")
        raise


def send_normal_email(data):
    """
    Send a standard email using the provided dictionary fields.
    """
    recipient = data.get("to_email")
    logger.info(f"Sending email to {recipient}")

    try:
        email = EmailMessage(
            subject=data["email_subject"],
            body=data["email_body"],
            from_email=settings.EMAIL_FROM,
            to=[recipient],
        )
        email.send()
        logger.info(f"Email successfully sent to {recipient}")

    except Exception as e:
        logger.exception(f"Failed to send email to {recipient}: {str(e)}")
        raise


def get_user_by_email(email):
    """
    Retrieve a user by email address.
    """
    try:
        return User.objects.get(email=email)

    except User.DoesNotExist:
        logger.warning(f"No user found with email: {email}")
        return None


def check_existing_active_otp(user, code_type):
    """
    Check if the user has an active (non-expired, unused) OTP for the given code type.
    """
    try:
        otp = Passcode.objects.get(user=user, code_type=code_type, is_used=False)

        if otp.expires_at >= timezone.now():
            logger.info(f"Found active OTP for user: {user.email}")
            return otp

    except Passcode.DoesNotExist:
        logger.debug(f"No active OTP for user {user.email} and code_type {code_type}")

    return None


def create_and_send_otp(user, code_type, purpose):
    """
    Utility to create an OTP for a user and send it via email.

    This function:
    1. Deletes all existing OTPs for the user and code_type
    2. Creates a new OTP with is_used=False
    3. Sends the OTP via email

    Args:
        user: User instance
        code_type: Type of OTP (VERIFICATION, PASSWORD_RESET, etc.)
        purpose: Email purpose ('verification' or 'password_reset')

    Returns:
        tuple: (otp_object, error_message, status_code)
               - On success: (otp, None, None)
               - On failure: (None, error_message, status_code)
    """
    try:
        otp = create_otp_for_user(user, code_type=code_type)
        logger.info(f"OTP successfully created for user: {user.email}")

    except Exception as e:
        logger.error(f"Error creating OTP for user {user.email}: {str(e)}")
        return None, _("Failed to create OTP."), status.HTTP_500_INTERNAL_SERVER_ERROR

    otp.expiry_text = format_expiry_time(otp.expires_at)

    try:
        send_code_to_user(
            email=user.email,
            otp_code=otp.code,
            purpose=purpose,
            expiry_text=otp.expiry_text,
        )
        logger.info(f"OTP email successfully sent to {user.email}")

    except Exception as e:
        logger.error(f"Error sending OTP email to {user.email}: {str(e)}")
        otp.delete()
        return (
            None,
            _("Failed to send OTP email."),
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return otp, None, None


def format_expiry_time(expiry_datetime):
    """
    Convert an expiry datetime to a human-readable countdown format (minutes and seconds).
    """

    remaining = expiry_datetime - timezone.now()
    total_seconds = int(remaining.total_seconds())

    if total_seconds <= 0:
        return "expired"

    minutes, seconds = divmod(total_seconds, 60)
    parts = []

    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    if seconds:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return ", ".join(parts)
