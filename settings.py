DB_SETTINGS = {
    'host': 'localhost',
    'user': 'webpush',
    'password': 'password',
    'database': 'webpush',
}

VAPID_CLAIMS_SUB_MAILTO = "mail@example.com"
SCHEDULE_EVERY_MINUTES = 1
CORS_ORIGINS = ""


WEB_SETTINGS = {
    'host': 'localhost',
    'port': '8080',
    'cors_origins': 'http://localhost:8081',
    'debug': True,
    'use_reloader': False,
}

import logging
from logging.handlers import TimedRotatingFileHandler

# Create a timed rotating file handler
handler = TimedRotatingFileHandler(
    'app.log',           # Log file name
    when='midnight',     # Rotate at midnight (other options: 'S', 'M', 'H', 'D', 'W0' for days of the week)
    interval=1,          # Rotate every day
    backupCount=7        # Keep logs for the last 7 days
)

# Configure the logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[handler]
)