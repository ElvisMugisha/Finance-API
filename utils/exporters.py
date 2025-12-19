import csv
import os
from typing import List, Dict
import pandas as pd
from django.http import HttpResponse
from django.conf import settings
from utils import loggings

logger = loggings.setup_logging()


# Fixed headers consistent with flatten_user_data
USER_FIELDS = [
    "id",
    "email",
    "username",
    "first_name",
    "middle_name",
    "last_name",
    "is_premium",
    "premium_expires",
    "is_active",
    "is_verified",
    "is_staff",
    "is_superuser",
    "last_login",
    "last_activity_at",
    "created_at",
    "updated_at",
]

PROFILE_FIELDS = [
    "bio",
    "phone_number",
    "date_of_birth",
    "profile_picture",
    "gender",
    "occupation",
    "annual_income",
    "currency_preference",
    "notification_preferences",
    "privacy_settings",
    "created_at",
    "updated_at",
]

EXCEL_HEADERS = USER_FIELDS + [f"profile_{field}" for field in PROFILE_FIELDS]


def flatten_user_data(users: List[Dict]) -> List[Dict]:
    """
    Flatten nested profile data into top-level fields for export with consistent headers.

    Args:
        users (List[Dict]): List of serialized user dictionaries.

    Returns:
        List[Dict]: Flattened list ready for export.
    """
    flattened = []
    for user in users:
        row = {}
        profile = user.get("profile") or {}  # Safe fallback

        # Populate user fields
        for field in USER_FIELDS:
            row[field] = user.get(field, "")

        # Populate profile fields with 'profile_' prefix
        for field in PROFILE_FIELDS:
            row[f"profile_{field}"] = profile.get(field, "")

        flattened.append(row)

    return flattened


def export_to_excel(data: List[Dict], file_path: str) -> None:
    """
    Save data to an Excel file with proper table formatting.

    Args:
        data (List[Dict]): List of dictionaries to export.
        file_path (str): Full path to save Excel file.
    """
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Create DataFrame
        df = pd.DataFrame(data, columns=EXCEL_HEADERS)

        # Save to Excel with formatting
        with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Users")

            # Access worksheet to apply simple formatting
            ws = writer.sheets["Users"]
            for col_cells in ws.columns:
                max_length = 0
                column = col_cells[0].column_letter  # Get the column name
                for cell in col_cells:
                    try:
                        cell_value = str(cell.value) if cell.value is not None else ""
                        max_length = max(max_length, len(cell_value))
                    except Exception:
                        pass
                adjusted_width = max_length + 2
                ws.column_dimensions[column].width = adjusted_width

        logger.info(f"Excel saved successfully at {file_path}, {len(data)} rows")

    except Exception as e:
        logger.exception(f"Excel export failed: {str(e)}")
        raise
