# Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt


# Final
FROM python:3.12-slim

# Create a non-root user
RUN addgroup --system app && adduser --system --group app

WORKDIR /app

# Install runtime dependencies (libpq-dev is needed for verify, netcat for entrypoint, gosu for user switching)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev netcat-openbsd gosu && \
    rm -rf /var/lib/apt/lists/*

# Copy wheels from builder
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir /wheels/*

# Copy entrypoint script first and fix it
COPY entrypoint.sh /app/entrypoint.sh

# Fix line endings and set permissions for entrypoint
RUN sed -i 's/\r$//' /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh

# Copy rest of project
COPY . .

# Chown all the files to the app user
RUN chown -R app:app /app

# Switch to non-root user (Moved to entrypoint for permission handling)
# USER app

# Expose port
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# Run the application using Gunicorn for production
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]