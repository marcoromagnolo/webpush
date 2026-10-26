# Scheduler: the weekly slots of the enabled sites (table schedule_slot) on the library schedule, in local time
# (TZ of the container). A slot runs in a pool of threads (notifier.send_slot): a long sending doesn't delay the
# slots of the other sites; two sendings of the same site never overlap.
# reload() registers the slots again after a change in the console (pages Schedules and Sites).
# A site without schedule (use_schedule false) sends every message at its send_after: every DUE_SECONDS the loop
# sends the messages whose time has come (notifier.send_due), in the same pool.
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import schedule

import db
import logg
import notifier
import settings
import util

logger = logg.create_logger('scheduler')

DAYS = {1: 'monday', 2: 'tuesday', 3: 'wednesday', 4: 'thursday', 5: 'friday', 6: 'saturday', 7: 'sunday'}
DAY_LABELS = {1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday', 7: 'Sunday'}

CLEANUP_SECONDS = 300
DUE_SECONDS = 15  # the messages of the sites without schedule leave at most this late

_reload = threading.Event()
_stop = threading.Event()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='slot')
_running = set()  # sites with a sending running
_running_lock = threading.Lock()


def register():
    """Register the slots of the enabled sites (the previous ones are removed)."""
    schedule.clear('slot')
    enabled = {site['id'] for site in db.get_sites(enabled_only=True) if site['use_schedule']}
    count = 0
    for slot in db.get_slots():
        if slot['site_id'] not in enabled:
            continue
        at = f"{slot['hour']:02}:{slot['minute']:02}"
        job = getattr(schedule.every(), DAYS[slot['day']]).at(at)
        job.do(run_slot, slot['site_id'], slot['day'], slot['hour'], slot['minute']).tag('slot')
        count += 1
    logger.info(f"{count} slots of {len(enabled)} enabled sites with schedule registered")


def run_slot(site_id, day, hour, minute):
    _executor.submit(_run_slot, site_id, day, hour, minute)


def _run_slot(site_id, day, hour, minute):
    with _running_lock:
        if site_id in _running:
            logger.warning(f"Slot {hour:02}:{minute:02} of site {site_id} skipped: the previous sending is still running")
            return
        _running.add(site_id)
    try:
        notifier.send_slot(site_id, day, hour, minute)
    except Exception as e:
        logger.error(f"Slot {hour:02}:{minute:02} of site {site_id} failed: {e}", exc_info=True, extra={'site_id': site_id})
    finally:
        with _running_lock:
            _running.discard(site_id)


def run_due():
    """The sites without schedule that have messages whose time has come: a sending each (not two at once)."""
    for site_id in db.get_sites_with_due_messages():
        with _running_lock:
            if site_id in _running:
                continue  # its sending is still running: the next messages go with it or at the next check
            _running.add(site_id)
        _executor.submit(_run_due, site_id)


def _run_due(site_id):
    try:
        notifier.send_due(site_id)
    except Exception as e:
        logger.error(f"Sending at the time of the messages of site {site_id} failed: {e}", exc_info=True,
                     extra={'site_id': site_id})
    finally:
        with _running_lock:
            _running.discard(site_id)


def reload():
    """Register the slots again at the next second (after a change in the console)."""
    _reload.set()


def loop():
    logger.info('Scheduler running')
    _reload.set()  # first registration inside the loop: a database not available at the start is retried
    last_cleanup = last_due = 0
    while not _stop.is_set():
        # an error must not stop the scheduler thread: the next slots still run
        try:
            if time.monotonic() - last_cleanup > CLEANUP_SECONDS:
                # expired failed requests of the ip blacklist and errors older than ERROR_LOG_RETENTION_DAYS
                last_cleanup = time.monotonic()
                util.clean_ip_blacklist()
                db.delete_error_logs(older_than_days=settings.ERROR_LOG['retention_days'])
            if _reload.is_set():
                register()
                _reload.clear()  # after: a failed registration is retried
            schedule.run_pending()
            if time.monotonic() - last_due >= DUE_SECONDS:
                last_due = time.monotonic()
                run_due()
        except Exception as e:
            logger.error(f"Scheduler error: {e}", exc_info=True)
        _stop.wait(1)
    logger.info('Scheduler stopped')


def start():
    threading.Thread(target=loop, daemon=True, name='scheduler').start()


def stop(timeout):
    """Stop the scheduler and wait up to timeout seconds for the sendings running."""
    _stop.set()
    _executor.shutdown(wait=False, cancel_futures=True)
    waiter = threading.Thread(target=_executor.shutdown, kwargs={'wait': True})
    waiter.start()
    waiter.join(timeout)
    return not waiter.is_alive()


def is_running(site_id):
    with _running_lock:
        return site_id in _running
