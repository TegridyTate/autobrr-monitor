FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install cron and necessary dependencies
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the Python script and entrypoint script
COPY autobrr_monitor.py .
COPY entrypoint.sh .

# Ensure the entrypoint script is executable
RUN chmod +x /app/entrypoint.sh

# Set environment variables
ENV QBITTORRENT_HOST=""
ENV QBITTORRENT_PORT=""
ENV QBITTORRENT_USERNAME=""
ENV QBITTORRENT_PASSWORD=""

ENV PROMETHEUS_HOST=""
ENV PROMETHEUS_PORT=""

ENV AUTOBRR_HOST=""
ENV AUTOBRR_PORT=""
ENV AUTOBRR_API_KEY=""

ENV TORRENT_CATEGORY_FILTER="autobrr"

# Run the monitor every minute by default
ENV MONITOR_INTERVAL_MINUTES=1
ENV GLOBAL_UPLOAD_THRESHOLD_BYTES=1048576
ENV GLOBAL_TIME_HORIZON_SECONDS=43200
ENV TORRENT_UPLOAD_THRESHOLD_BYTES=1048576
ENV TORRENT_TIME_HORIZON_SECONDS=432000

# Max 100 GB allocated for autobrr
ENV MAX_TORRENTS_SIZE_BYTES=1073741824

ENV ENFORCE_MAX_SIZE_POLICY="relaxed"

ENV AUTOBRR_INDEXER_NAME=""

ENV SIMULATION_MODE=1

ENV DEBUG=1

# Use the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]
