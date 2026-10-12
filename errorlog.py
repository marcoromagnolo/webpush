# Errors of the sendings, the scheduler, the API and the web app saved in the table error_log (console page Errors).
#
# A logging handler on the root logger saves every record of level ERROR or more, without changing the code that
# logs them (as in scrapynews). The site and the message of an error come from the thread running the sending
# (context) or from the log call: extra={'site_id': .., 'message_id': .., 'url': ..};
# extra={'error_log': False} keeps a record out of the table (e.g. "database not available").
import logging
import re
import threading
import time
import traceback as tb
from contextlib import contextmanager

import settings

PHASES = {'push': 'Push', 'scheduler': 'Scheduler', 'api': 'API', 'app': 'App'}
PHASE_BADGES = {'push': 'bg-primary', 'scheduler': 'bg-info text-dark', 'api': 'bg-warning text-dark', 'app': 'bg-secondary'}

# phase of the errors by logger name
LOGGER_PHASES = {'notifier': 'push', 'scheduler': 'scheduler', 'api': 'api'}

MAX_MESSAGE = 2000
MAX_TRACEBACK = 60000
PAUSE_AFTER_FAILURE = 60  # seconds without saving after the database didn't answer

URL_RE = re.compile(r"""https?://[^\s'"<>)\]]+""")

_context = threading.local()


@contextmanager
def context(site_id=None, message_id=None):
    """Errors logged by this thread inside the block belong to the site and the message (e.g. a sending)."""
    previous = getattr(_context, 'value', None)
    _context.value = {'site_id': site_id, 'message_id': message_id}
    try:
        yield
    finally:
        _context.value = previous


class DBErrorHandler(logging.Handler):

    def __init__(self):
        super().__init__(logging.ERROR)
        self.saved = 0
        self.paused_until = 0
        self.local = threading.local()  # a log call made while saving an error is not saved (no loops)
        self.count_lock = threading.Lock()

    def emit(self, record):
        if getattr(record, 'error_log', True) is False or getattr(self.local, 'busy', False):
            return
        if time.monotonic() < self.paused_until:
            return
        with self.count_lock:
            if self.saved > settings.ERROR_LOG['max_per_process']:
                return
            self.saved += 1
            limit_reached = self.saved > settings.ERROR_LOG['max_per_process']

        self.local.busy = True
        try:
            import db  # here: db imports modules that log
            error = self.to_error(record)
            if limit_reached:
                error.update(message=f"More than {settings.ERROR_LOG['max_per_process']} errors in this process: "
                                     f"the next ones are only in the log files (log/)", traceback=None, url=None)
            db.insert_error_log(error)
        except Exception:
            # database not available: errors stay in the log files only, retry later
            self.paused_until = time.monotonic() + PAUSE_AFTER_FAILURE
        finally:
            self.local.busy = False

    @staticmethod
    def to_error(record):
        context_value = getattr(_context, 'value', None) or {}
        message = record.getMessage()

        traceback = None
        if record.exc_info and record.exc_info[1] is not None:
            traceback = ''.join(tb.format_exception(*record.exc_info))
        elif record.exc_text:
            traceback = record.exc_text

        url = getattr(record, 'url', None)
        if not url:
            match = URL_RE.search(message)
            url = match.group(0) if match else None

        return {
            'phase': LOGGER_PHASES.get(record.name, 'app'),
            'source': record.name[:100],
            'site_id': getattr(record, 'site_id', None) or context_value.get('site_id'),
            'message_id': getattr(record, 'message_id', None) or context_value.get('message_id'),
            'url': url[:2000] if url else None,
            'message': message.strip()[:MAX_MESSAGE] or record.levelname,
            'traceback': traceback[-MAX_TRACEBACK:] if traceback else None,
        }


def install():
    """Save the errors of this process in the table error_log."""
    root = logging.getLogger()
    if not any(isinstance(handler, DBErrorHandler) for handler in root.handlers):
        root.addHandler(DBErrorHandler())
