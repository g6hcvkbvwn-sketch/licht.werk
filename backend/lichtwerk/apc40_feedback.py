"""Sends the APC40 LED state (buttons, RGB pads, knob rings) computed by `Apc40Control.leds`.

The state is compared a few times per second and only changes are sent. After a (re)connect or
the introduction message the device state is unknown, so everything is sent again.
"""

import logging
import threading
from collections.abc import Callable
from typing import Optional

from .apc40_control import LedState

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.1


class Apc40Feedback:
    def __init__(
        self,
        desired: Callable[[], LedState],
        send_note: Callable[[int, int, int], bool],
        send_cc: Callable[[int, int, int], bool],
        connection_id: Callable[[], int],
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._desired = desired
        self._send_note = send_note
        self._send_cc = send_cc
        self._connection_id = connection_id
        self._poll = poll_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._sent: LedState = {}
        self._sent_connection = -1
        self._epoch = 0  # bumped by invalidate()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="apc40-leds", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def invalidate(self) -> None:
        """Forget what was sent, e.g. after the introduction message reset the device."""
        with self._lock:
            self._sent = {}
            self._epoch += 1

    def update(self) -> None:
        connection = self._connection_id()
        with self._lock:
            if connection != self._sent_connection:
                self._sent = {}
            sent = dict(self._sent)
            epoch = self._epoch
        desired = self._desired()
        for key, value in desired.items():
            if sent.get(key) == value:
                continue
            kind, channel, number = key
            send = self._send_note if kind == "note" else self._send_cc
            if not send(channel, number, value):
                return  # no output; try again next round
            sent[key] = value
        with self._lock:
            if epoch == self._epoch:
                self._sent = sent
                self._sent_connection = connection

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.update()
            except Exception:
                logger.exception("Unexpected failure while updating APC40 LEDs")
            self._stop.wait(self._poll)
