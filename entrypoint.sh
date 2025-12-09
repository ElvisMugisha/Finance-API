#!/bin/sh

# Fix permission for media directory (which might be a volume)
if [ -d "/app/media" ]; then
    chown -R app:app /app/media
fi

# Fix permission for staticfiles directory (if used)
if [ -d "/app/staticfiles" ]; then
    chown -R app:app /app/staticfiles
fi

# If the command is running gunicorn or something similar that needs the DB
if [ "$DATABASE" = "postgres" ]
then
    echo "Waiting for postgres..."

    while ! nc -z $POSTGRES_HOST $POSTGRES_PORT; do
      sleep 0.1
    done

    echo "PostgreSQL started"
fi

# Execute the passed command as app user
exec gosu app "$@"
