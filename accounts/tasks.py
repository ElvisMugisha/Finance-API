from celery import shared_task
from django.utils import timezone

from utils import loggings

from .models import RecurringTransaction

logger = loggings.setup_logging()


@shared_task(name="accounts.tasks.process_recurring_transactions")
def process_recurring_transactions():
    """
    Automated task to process all active recurring transactions that are due.
    This should be scheduled to run once a day (e.g., at midnight).
    """
    today = timezone.now().date()

    # Fetch all active recurring transactions where next_due_date is today or in the past
    recurring_items = RecurringTransaction.objects.filter(
        is_active=True, next_due_date__lte=today
    )

    logger.info(
        f"Starting automated processing of {recurring_items.count()} recurring transactions."
    )

    count = 0
    for item in recurring_items:
        try:
            # item.process_due handles transaction creation and schedule bumping
            tx = item.process_due(execution_date=today)
            if tx:
                count += 1
                logger.info(
                    f"Created transaction {tx.id} for recurring profile: {item.name}"
                )
        except Exception as e:
            logger.error(
                f"Critical error processing recurring transaction {item.id}: {str(e)}"
            )

    return f"Successfully processed {count} recurring transactions."
