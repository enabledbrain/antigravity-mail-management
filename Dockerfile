# Lightweight Alpine Linux based Python 3.12 image
FROM python:3.12-alpine

# Install timezone data
RUN apk add --no-cache tzdata

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application script and rules
COPY mail_scanner.py slack_listener.py ./
COPY rules ./rules

# Create directory for persistent data
RUN mkdir -p /app/data

# Default environment variables
ENV CONFIG_PATH=/app/config.json
ENV DB_FILE=/app/data/mail_history.db
ENV MEILI_URL=http://search-engine:7700
ENV RULES_PATH=/app/rules/summary_rules.md
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3"]
CMD ["mail_scanner.py"]
