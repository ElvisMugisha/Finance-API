from typing import List

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import connection

from utils import loggings

logger = loggings.setup_logging()


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


def get_index_name(table_name: str, fields: List[str]) -> str:
    """
    Get appropriate index name based on database backend.

    Args:
        table_name: Table name
        fields: List of field names

    Returns:
        Database-appropriate index name
    """
    vendor = connection.vendor

    if vendor == "sqlite":
        # SQLite: ≤ 30 chars
        base_name = f"idx_{table_name[:8]}_{'_'.join(f[:3] for f in fields)}"
        return base_name[:30]

    elif vendor == "postgresql":
        # PostgreSQL: ≤ 63 chars, can be descriptive
        return f"idx_{table_name}_{'_'.join(fields)}"

    elif vendor == "mysql":
        # MySQL: ≤ 64 chars
        return f"idx_{table_name[:10]}_{'_'.join(fields)}"[:64]

    else:
        # Default: safe 30 chars
        return f"idx_{table_name[:8]}_{'_'.join(f[:3] for f in fields)}"[:30]
