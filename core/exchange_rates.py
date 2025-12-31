from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils.timezone import now

from core.models import Currency
from utils import loggings

# Initialize logger
logger = loggings.setup_logging()


def fetch_from_open_exchange_rates():
    """
    Fetch exchange rates from OpenExchangeRates API.
    """
    if not settings.OXR_API_KEY:
        logger.warning("OpenExchangeRates API key is not configured.")
        raise ValueError("OXR_API_KEY is missing in settings.")

    url = f"https://openexchangerates.org/api/latest.json?app_id={settings.OXR_API_KEY}"
    logger.info("Fetching rates from OpenExchangeRates...")
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def fetch_from_currency_layer():
    """
    Fetch exchange rates from CurrencyLayer API.
    """
    if not settings.CURRENCY_LAYER_API_KEY:
        logger.warning("CurrencyLayer API key is not configured.")
        raise ValueError("CURRENCY_LAYER_API_KEY is missing in settings.")

    url = (
        f"http://api.currencylayer.com/live?access_key={settings.CURRENCY_LAYER_API_KEY}"
        f"&source={settings.BASE_CURRENCY}&format=1"
    )
    logger.info("Fetching rates from CurrencyLayer...")
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()

    if not data.get("success"):
        raise ValueError(
            "CurrencyLayer error: " + str(data.get("error", "Unknown error"))
        )

    # Normalize format to match OpenExchangeRates
    normalized = {
        "base": data["source"],
        "rates": {k.replace(data["source"], ""): v for k, v in data["quotes"].items()},
    }
    return normalized


def get_cached_or_fresh_rates():
    """
    Retrieve exchange rates from cache or fetch from APIs
    (OpenExchangeRates → fallback to CurrencyLayer).
    """
    cache_key = f"exchange_rates_response_{settings.BASE_CURRENCY}"
    cached = cache.get(cache_key)

    if cached:
        logger.info("Using cached exchange rates.")
        return cached

    logger.info("No cache found. Trying OpenExchangeRates first.")
    try:
        data = fetch_from_open_exchange_rates()
        data["_source"] = "OpenExchangeRates"  # Track source
        logger.info(f"Exchange rates source: {data['_source']}")

    except Exception as e:
        logger.warning(f"OpenExchangeRates failed: {e}")
        logger.info("Falling back to CurrencyLayer...")

        try:
            data = fetch_from_currency_layer()
            data["_source"] = "CurrencyLayer"  # Track fallback source
            logger.info(f"Exchange rates source: {data['_source']}")

        except Exception as fallback_err:
            logger.error(f"CurrencyLayer fallback failed: {fallback_err}")
            raise RuntimeError("Failed to fetch exchange rates from all sources.")

    # Cache the data with source tracking
    cache.set(cache_key, data, timeout=settings.CACHE_TTL)
    logger.info("Exchange rates cached successfully.")

    return data


def update_exchange_rates():
    """
    Updates Currency.exchange_rate based on OpenExchangeRates data (cached or fresh).
    """
    result = {"updated": [], "skipped": [], "errors": []}

    try:
        # Start Update Process
        logger.info(f"Starting update with base currency: {settings.BASE_CURRENCY}")
        data = get_cached_or_fresh_rates()
        logger.info(f"Rates fetched from: {data.get('_source', 'unknown')}")

        # Per-Currency Update Loop
        for currency in Currency.objects.filter(is_active=True):
            code = currency.code.upper()

            # Base currency: skip update but set exchange_rate = 1
            if code == settings.BASE_CURRENCY:
                try:
                    currency.exchange_rate = Decimal("1.0")  # Always 1 for base
                    currency.exchange_source = data.get("_source")
                    currency.exchange_updated_at = now()
                    currency.save(
                        update_fields=[
                            "exchange_rate",
                            "exchange_source",
                            "exchange_updated_at",
                            "updated_at",
                        ]
                    )

                    logger.debug(f"Skipped base currency '{code}' (rate=1.0)")
                    result["skipped"].append(code)

                except Exception as e:
                    logger.error(f"Error updating base currency {code}: {e}")
                    result["errors"].append(f"{code}: {str(e)}")
                continue

            try:
                base_to_currency = data["rates"].get(code)
                if not base_to_currency:
                    logger.warning(f"No exchange rate found for {code}. Skipping.")
                    result["skipped"].append(code)
                    continue

                # Validate and store
                rate = Decimal(str(base_to_currency))
                if rate <= 0:
                    raise InvalidOperation("Exchange rate must be positive")

                currency.exchange_rate = rate.quantize(
                    Decimal("0.0000000001")
                )  # Matching model decimal_places=10
                currency.exchange_source = data.get("_source")
                currency.exchange_updated_at = now()
                currency.save(
                    update_fields=[
                        "exchange_rate",
                        "exchange_source",
                        "exchange_updated_at",
                        "updated_at",
                    ]
                )

                logger.info(
                    f"Updated {code}: 1 {settings.BASE_CURRENCY} = {currency.exchange_rate} {code}"
                )
                result["updated"].append(code)

            except (InvalidOperation, TypeError, ValueError) as e:
                logger.error(f"Invalid rate for {code}: {base_to_currency} | {e}")
                result["errors"].append(f"{code}: {e}")

            except Exception as e:
                logger.exception(f"Unhandled error updating {code}: {e}")
                result["errors"].append(f"{code}: {e}")

        # Wrap-up and Return
        logger.info("Exchange rate update process completed.")
        return result

    except Exception as e:
        logger.exception("Exchange rate update failed.")
        raise RuntimeError(f"Exchange rate update failed: {str(e)}")
