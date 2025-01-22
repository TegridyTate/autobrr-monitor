#!/bin/bash

# Make sure the MONITOR_INTERVAL_MINUTES variable is valid and replace it with a fixed value if necessary
if [ -z "$MONITOR_INTERVAL_MINUTES" ]; then
  echo "MONITOR_INTERVAL_MINUTES not set. Defaulting to 1 minute."
  MONITOR_INTERVAL_MINUTES=1
fi

# Create a user with the correct PUID and PGID if they don't exist
groupadd -g $PGID autobrr
useradd -u $PUID -g $PGID -m autobrr

# Write all environment variables to /etc/environment so cron can access them
echo "Writing environment variables to /etc/environment"
printenv | grep -v "no_proxy" >> /etc/environment

# Set up the cron job to run every $MONITOR_INTERVAL_MINUTES minute(s)
echo "Setting up cron job to run autobrr_monitor.py every $MONITOR_INTERVAL_MINUTES minute(s)"

# Add the cron job that runs as the "autobrr" user
echo "*/$MONITOR_INTERVAL_MINUTES * * * * autobrr /usr/local/bin/python3 /app/autobrr_monitor.py >> /var/log/cron.log 2>&1" > /etc/cron.d/monitor_cron

# Set permissions for the cron file
chmod 0644 /etc/cron.d/monitor_cron

# Apply the cron job
crontab /etc/cron.d/monitor_cron

# Ensure the log file exists
touch /var/log/cron.log
chmod 666 /var/log/cron.log

# Start the cron service
echo "Starting cron service..."
service cron start

# Keep the container running by tailing the log file
echo "Tailing log file to keep container running..."
tail -f /var/log/cron.log
