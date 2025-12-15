import re

from decouple import config
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import Throttled
from rest_framework.throttling import UserRateThrottle


class CustomScopedRateThrottle(UserRateThrottle):
    """
    A reusable, dynamic scoped throttle class with extended support for custom rate formats.

    Features:
    - Supports rate formats like '10/90s', '3/2h', '5/1d', etc.
    - Allows rate definitions per user tier (anonymous, authenticated, premium).
    - Caches the parsed rate per request for efficiency.
    """

    # These should be set in subclasses
    scope: str = "custom"
    anon_rate: str = None
    user_rate: str = None
    premium_rate: str = None

    # Mapping for time unit conversion to seconds
    TIME_UNITS = {
        "s": 1,  # seconds
        "m": 60,  # minutes
        "h": 3600,  # hours
        "d": 86400,  # days
    }

    def parse_rate(self, rate):
        """
        Parse rate string like '10/30m' into (num_requests, duration in seconds).
        """
        if not rate:
            return None, None

        try:
            match = re.match(r"(\d+)/(\d+)([smhd])$", rate.strip())
            if not match:
                raise ValueError(
                    f"Invalid rate format: '{rate}'. Expected format like '5/30m'."
                )

            num_requests = int(match.group(1))
            multiplier = int(match.group(2))
            unit = match.group(3)

            if unit not in self.TIME_UNITS:
                raise ValueError(f"Invalid time unit '{unit}' in rate: {rate}")

            duration = multiplier * self.TIME_UNITS[unit]
            return num_requests, duration

        except Exception:
            return None, None

    def get_rate_tuple(self, request):
        """
        Determine the applicable rate for the request based on user type and cache the result.
        """
        if hasattr(request, "_cached_throttle_rate"):
            return request._cached_throttle_rate

        try:
            if request.user and request.user.is_authenticated:
                if getattr(request.user, "is_premium", False) and self.premium_rate:
                    rate_str = self.premium_rate
                else:
                    rate_str = self.user_rate
            else:
                rate_str = self.anon_rate

            self.rate = rate_str
            parsed = self.parse_rate(rate_str)

            if parsed == (None, None):
                return None, None

            request._cached_throttle_rate = parsed
            return parsed

        except Exception:
            return None, None

    def get_cache_key(self, request, view):
        """
        Returns a unique cache key based on the user or IP and throttle scope.
        """
        try:
            if request.user and request.user.is_authenticated:
                ident = request.user.pk

            else:
                ident = self.get_ident(request)

            key = self.cache_format % {"scope": self.scope, "ident": ident}
            return key

        except Exception:
            raise Throttled(detail=_("Error determining request rate limit."))

    def allow_request(self, request, view):
        """
        Determines if the request should be throttled. Saves request for later use.
        """
        # Bypass throttling if LOAD_TESTING is enabled
        if config("LOAD_TESTING", default=False, cast=bool):
            return True

        self.request = request  # Store for `wait()` fallback
        self.num_requests, self.duration = self.get_rate_tuple(request)

        if self.num_requests is None or self.duration is None:
            return True  # Fail open if throttle rate misconfigured

        return super().allow_request(request, view)

    def wait(self):
        """
        Returns the remaining time (in seconds) before the request can be retried.
        """
        # Ensure duration is available (in case called directly)
        if getattr(self, "duration", None) is None:
            try:
                self.num_requests, self.duration = self.get_rate_tuple(self.request)

            except Exception:
                return 60  # Reasonable default fallback

        wait_time = super().wait()
        return wait_time


class OTPRequestRateThrottle(CustomScopedRateThrottle):
    """
    Throttle class for controlling the rate of OTP requests.

    - Anonymous users: 2 requests per 30 minutes.
    - Authenticated users: 5 requests per 30 minutes.
    - Premium users: 10 requests per 30 minutes.
    """

    scope = "otp_request"
    anon_rate = "2/30m"
    user_rate = "5/30m"
    premium_rate = "10/30m"


class LoginRateThrottle(CustomScopedRateThrottle):
    """
    Throttle class for login attempts.

    - Anonymous users: 3 requests per 30 minutes.
    - Authenticated users: 5 requests per hour.
    - Premium users: 100 requests per hour.
    """

    scope = "login"
    anon_rate = "3/30m"
    user_rate = "5/1h"
    premium_rate = "100/1h"


class PasswordChangeRateThrottle(CustomScopedRateThrottle):
    """
    Throttle class for password change requests.

    - Anonymous users: 1 request per day.
    - Authenticated users: 2 requests per day.
    - Premium users: 5 requests per day.
    """

    scope = "password_change"
    anon_rate = "1/1d"
    user_rate = "2/1d"
    premium_rate = "5/1d"


class RequestPasswordResetRateThrottle(CustomScopedRateThrottle):
    """
    Throttle class for password reset requests.

    - Anonymous users: 2 requests in 2 days.
    - Authenticated users: 4 requests in 2 days.
    - Premium users: 6 requests in 2 days.
    """

    scope = "password_reset"
    anon_rate = "2/2d"
    user_rate = "4/2d"
    premium_rate = "6/2d"


class UniversalListThrottle(CustomScopedRateThrottle):
    """
    Throttle class for listing and creation universal objects (items).

    - Anonymous users: 20 requests per minute
    - Authenticated users: 60 requests per minute
    - Premium users: 120 requests per minute
    """

    scope = "universal_list"
    anon_rate = "20/1m"
    user_rate = "60/1m"
    premium_rate = "120/1m"


class BurstRateThrottle(CustomScopedRateThrottle):
    """
    Throttle class for burst protection on high-frequency endpoints.

    Prevents rapid-fire requests that could overwhelm the system.

    - Anonymous users: 10 requests per 10 seconds
    - Authenticated users: 30 requests per 10 seconds
    - Premium users: 60 requests per 10 seconds
    """

    scope = "burst"
    anon_rate = "10/10s"
    user_rate = "30/10s"
    premium_rate = "60/10s"


class SustainedRateThrottle(CustomScopedRateThrottle):
    """
    Throttle class for sustained usage over longer periods.

    Prevents resource exhaustion from sustained high usage.

    - Anonymous users: 500 requests per day
    - Authenticated users: 5000 requests per day
    - Premium users: 20000 requests per day
    """

    scope = "sustained"
    anon_rate = "500/1d"
    user_rate = "5000/1d"
    premium_rate = "20000/1d"


class TransactionThrottle(CustomScopedRateThrottle):
    """Custom throttle for transaction endpoints."""

    scope = "transaction"
    anon_rate = "100/1h"
    user_rate = "100/1h"
    premium_rate = "100/1h"
