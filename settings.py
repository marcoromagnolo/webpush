# Settings
import os
from pathlib import Path
from dotenv import load_dotenv

# Secrets and environment-specific values are read from .env (see .env.example)
load_dotenv(Path(__file__).resolve().parent / '.env')


def _required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable '{name}' (see .env.example)")
    return value


def _bool(name, default='false'):
    return os.getenv(name, default).lower() == 'true'


WEB_SETTINGS = {
    'host': os.getenv('WEB_HOST', '127.0.0.1'),
    'port': int(os.getenv('WEB_PORT', '8080')),
    'debug': _bool('WEB_DEBUG'),
    'use_reloader': False,
    'threads': int(os.getenv('WEB_THREADS', '8')),
    # behind a reverse proxy (nginx): read the client ip and the scheme from the X-Forwarded-* headers
    'behind_proxy': _bool('WEB_BEHIND_PROXY'),
    # pages Home, Messages, Subscribers and Errors refreshed automatically: default seconds (0 = off), changeable on the page
    'refresh_seconds': int(os.getenv('REFRESH_SECONDS', '15')),
    'failed_login_block_threshold': 3,
    'failed_login_block_duration': 60,
}

# Public url of this application as the browsers of the sites reach it (e.g. https://www.example.com/notify):
# base of the API in the integration snippet and of the click tracking sent with the notifications
PUBLIC_URL = os.getenv('PUBLIC_URL', f"http://{WEB_SETTINGS['host']}:{WEB_SETTINGS['port']}").rstrip('/')

# Connection to MariaDB
DB_SETTINGS = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': _required('DB_USER'),
    'password': _required('DB_PASSWORD'),
    'database': _required('DB_NAME'),
    # seconds to wait for a new connection: a slow or unreachable DB doesn't block the app
    'connection_timeout': int(os.getenv('DB_CONNECT_TIMEOUT', '10')),
    # seconds to wait for the result of a query / to send it: a stuck DB doesn't hold a thread forever
    'read_timeout': int(os.getenv('DB_READ_TIMEOUT', '60')),
    'write_timeout': int(os.getenv('DB_READ_TIMEOUT', '60')),
}

# Connection pool of the process: see dbpool.py
DB_POOL = {
    'size': int(os.getenv('DB_POOL_SIZE', '10')),  # max connections of the process
    'timeout': int(os.getenv('DB_POOL_TIMEOUT', '10')),  # seconds to wait for a free connection
    'recycle': int(os.getenv('DB_POOL_RECYCLE', '1800')),  # idle connections older than this are reopened
}

# Sending of the notifications (the settings of each site are in the console, page Sites)
PUSH = {
    'workers': int(os.getenv('PUSH_WORKERS', '10')),  # parallel requests to the push services
    'timeout': int(os.getenv('PUSH_TIMEOUT', '10')),  # seconds to wait for a push service
    # consecutive failures (not 404 / 410, which remove the subscriber at once) before a subscriber is removed
    'max_failures': int(os.getenv('PUSH_MAX_FAILURES', '5')),
}

# Site of the old endpoints /subscription/ and /push_message/ (clients not yet moved to /api/v1)
DEFAULT_SITE = os.getenv('DEFAULT_SITE') or None

LOG_SETTINGS = {
    'level': os.getenv('LOG_LEVEL', 'INFO'),
    'path': 'log',
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
}

# Errors of the sendings, the scheduler and the web app saved in the table error_log (page Errors)
ERROR_LOG = {
    'retention_days': int(os.getenv('ERROR_LOG_RETENTION_DAYS', '30')),  # older errors are deleted
    'max_per_process': int(os.getenv('ERROR_LOG_MAX_PER_PROCESS', '500')),
}

# JWT of the console
JWT_SECRET_KEY = _required('JWT_SECRET_KEY')
JWT_ACCESS_TOKEN_EXPIRES = 3  # hours
