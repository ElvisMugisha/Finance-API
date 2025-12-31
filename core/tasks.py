from celery import shared_task

from core.exchange_rates import update_exchange_rates
from utils import loggings

logger = loggings.setup_logging()


@shared_task(name="core.tasks.update_all_exchange_rates")
def update_all_exchange_rates_task():
    """
    Automated task to refresh all active exchange rates from external providers.
    Recommended to run every 1-6 hours depending on production needs.
    """
    logger.info("Starting automated exchange rate update.")

    try:
        result = update_exchange_rates()
        updated_count = len(result.get("updated", []))
        logger.info(f"Successfully updated {updated_count} currency exchange rates.")
        return f"Updated {updated_count} currencies."
    except Exception as e:
        logger.error(f"Failed to update exchange rates in background: {str(e)}")
        return "Failed to update exchange rates."
