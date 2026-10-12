# Connection pool of a process (web app, every crawl, every transform), configured in .env (DB_POOL_*).
#
# Unlike mysql.connector.pooling.MySQLConnectionPool it:
# - opens the connections only when they are needed (a crawl or a transform uses one or two),
# - waits up to DB_POOL_TIMEOUT seconds for a free connection instead of failing at once,
# - checks and reopens the connections outside of its lock (a slow DB doesn't block the other threads),
# - reopens the connections idle for more than DB_POOL_RECYCLE seconds (before MariaDB drops them).
import threading
import time

import mysql.connector


class PoolTimeout(Exception):
    """No free connection within the timeout."""


class PooledConnection:
    """A connection of the pool: close() gives it back to the pool, the rest is the real connection."""

    def __init__(self, pool, cnx):
        self._pool = pool
        self._cnx = cnx

    def close(self):
        if self._cnx is not None:
            cnx, self._cnx = self._cnx, None
            self._pool.release(cnx)

    def __getattr__(self, name):
        if self._cnx is None:
            raise mysql.connector.errors.OperationalError("The connection was given back to the pool")
        return getattr(self._cnx, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class ConnectionPool:

    def __init__(self, settings, size, timeout, recycle):
        self.settings = settings
        self.size = size
        self.timeout = timeout
        self.recycle = recycle
        self.idle = []  # (connection, last used), most recent at the end
        self.opened = 0  # connections of the process: idle + in use
        self.condition = threading.Condition()

    def get_connection(self):
        deadline = time.monotonic() + self.timeout
        with self.condition:
            while not self.idle and self.opened >= self.size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PoolTimeout(f"No free database connection after {self.timeout}s "
                                      f"({self.size} in use, DB_POOL_SIZE)")
                self.condition.wait(remaining)
            if self.idle:
                cnx, last_used = self.idle.pop()  # LIFO: reuse the most recent one
            else:
                cnx, last_used = None, None
                self.opened += 1  # reserve the slot, the connection is opened outside of the lock

        # checks and new connections outside of the lock
        try:
            if cnx is not None and (time.monotonic() - last_used > self.recycle or not self.alive(cnx)):
                self.discard(cnx)
                cnx = None
            if cnx is None:
                cnx = mysql.connector.connect(**self.settings)
            return PooledConnection(self, cnx)
        except Exception:
            with self.condition:
                self.opened -= 1
                self.condition.notify()
            raise

    @staticmethod
    def alive(cnx):
        try:
            cnx.ping(reconnect=False)
            return True
        except Exception:
            return False

    @staticmethod
    def discard(cnx):
        try:
            cnx.close()
        except Exception:
            pass

    def release(self, cnx):
        """Give back a connection: an open transaction is rolled back, a broken connection is closed."""
        try:
            cnx.rollback()
            reusable = True
        except Exception:
            reusable = False
            self.discard(cnx)
        with self.condition:
            if reusable:
                self.idle.append((cnx, time.monotonic()))
            else:
                self.opened -= 1
            self.condition.notify()

    def stats(self):
        with self.condition:
            return {'opened': self.opened, 'idle': len(self.idle), 'size': self.size}
