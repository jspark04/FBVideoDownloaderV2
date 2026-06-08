from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from .probe import CookieStatus


@dataclass
class JobRecord:
    when: str
    url: str
    category: str
    message: str


class AppState:
    def __init__(self, max_jobs: int = 20):
        self.cookie = CookieStatus()
        self.jobs: deque[JobRecord] = deque(maxlen=max_jobs)
        self._lock = Lock()

    def record_job(self, url: str, category: str, message: str) -> None:
        rec = JobRecord(datetime.now().strftime("%Y-%m-%d %H:%M"), url, category, message)
        with self._lock:
            self.jobs.appendleft(rec)

    def set_cookie(self, status: CookieStatus) -> None:
        with self._lock:
            self.cookie = status


state = AppState()
