"""MIDI input adapter: finds the controller, survives unplugging, never blocks lighting output.

Only this module (and the real port class in it) touches the MIDI library. The port provider is
injectable so tests run without a controller. Sent to devices: LED feedback (note messages) and
device setup SysEx (APC40 introduction), always from separate threads, never from the lighting path.
"""

import logging
import queue
import threading
from typing import Callable, Optional, Protocol

from .midi_control import MidiEvent

logger = logging.getLogger(__name__)

RESCAN_SECONDS = 2.0
QUEUE_SIZE = 2000


class MidiInputPort(Protocol):
    def close(self) -> None: ...


class MidiOutputPort(Protocol):
    def send_note(self, channel: int, note: int, velocity: int) -> None: ...

    def send_cc(self, channel: int, control: int, value: int) -> None: ...

    def send_sysex(self, data: list[int]) -> None:
        """Send a SysEx message; `data` is the bytes between F0 and F7."""
        ...

    def close(self) -> None: ...


class MidiPorts(Protocol):
    def names(self) -> list[str]: ...

    def output_names(self) -> list[str]: ...

    def open_output(self, name: str) -> MidiOutputPort: ...

    def open(self, name: str, callback: Callable[[MidiEvent], None]) -> MidiInputPort: ...


class MidoPorts:
    """Real ports through mido + python-rtmidi (imported lazily; needs no hardware to import)."""

    def __init__(self) -> None:
        import mido

        mido.set_backend("mido.backends.rtmidi")
        self._mido = mido

    def names(self) -> list[str]:
        return list(self._mido.get_input_names())

    def open(self, name: str, callback: Callable[[MidiEvent], None]) -> MidiInputPort:
        def on_message(message) -> None:
            event = to_event(message)
            if event is not None:
                callback(event)

        return self._mido.open_input(name, callback=on_message)

    def output_names(self) -> list[str]:
        return list(self._mido.get_output_names())

    def open_output(self, name: str) -> MidiOutputPort:
        return MidoOutput(self._mido.open_output(name), self._mido)


class MidoOutput:
    def __init__(self, port, mido) -> None:
        self._port = port
        self._mido = mido

    def send_note(self, channel: int, note: int, velocity: int) -> None:
        self._port.send(
            self._mido.Message("note_on", channel=channel - 1, note=note, velocity=velocity)
        )

    def send_cc(self, channel: int, control: int, value: int) -> None:
        self._port.send(
            self._mido.Message("control_change", channel=channel - 1, control=control, value=value)
        )

    def send_sysex(self, data: list[int]) -> None:
        self._port.send(self._mido.Message("sysex", data=data))

    def close(self) -> None:
        self._port.close()


def to_event(message) -> Optional[MidiEvent]:
    """Translate a mido message; anything other than note/CC is ignored."""
    channel = getattr(message, "channel", None)
    if channel is None:
        return None
    if message.type == "control_change":
        return MidiEvent("cc", channel + 1, message.control, message.value)
    if message.type == "note_on":
        return MidiEvent("note", channel + 1, message.note, message.velocity)
    if message.type == "note_off":
        return MidiEvent("note", channel + 1, message.note, 0)
    return None


class MidiInput:
    def __init__(
        self,
        ports: MidiPorts,
        control,
        device: str,
        rescan_seconds: float = RESCAN_SECONDS,
        on_connect: Optional[Callable[[], None]] = None,
    ) -> None:
        """`control` needs a `handle(event)` method. `on_connect` runs after every (re)connect,
        once the output is open (e.g. to send device setup SysEx)."""
        self._ports = ports
        self._control = control
        self._device = device.lower()
        self._rescan = rescan_seconds
        self._on_connect = on_connect
        self._queue: queue.Queue[MidiEvent] = queue.Queue(maxsize=QUEUE_SIZE)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._port: Optional[MidiInputPort] = None
        self._out: Optional[MidiOutputPort] = None
        self._generation = 0
        self._connected_name: Optional[str] = None
        self._available: list[str] = []
        self._last_error: Optional[str] = None

    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._supervise, name="midi-connect", daemon=True),
            threading.Thread(target=self._dispatch, name="midi-dispatch", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=3)
        self._disconnect()

    def connection_id(self) -> int:
        """Changes on every (re)connect so LED feedback knows to send everything again."""
        with self._lock:
            return self._generation

    def send_note(self, channel: int, note: int, velocity: int) -> bool:
        """Send an LED message. Returns False when no output is available or sending failed."""
        return self._send(lambda out: out.send_note(channel, note, velocity))

    def send_cc(self, channel: int, control: int, value: int) -> bool:
        """Send a controller message (e.g. APC40 knob LED rings). False if it could not be sent."""
        return self._send(lambda out: out.send_cc(channel, control, value))

    def send_sysex(self, data: list[int]) -> bool:
        return self._send(lambda out: out.send_sysex(data))

    def _send(self, action: Callable[[MidiOutputPort], None]) -> bool:
        with self._lock:
            out = self._out
        if out is None:
            return False
        try:
            action(out)
        except Exception as error:
            self._set_error(f"Sending MIDI to the controller failed: {error}")
            return False
        return True

    def status(self) -> dict:
        with self._lock:
            return {
                "device_filter": self._device,
                "connected": self._connected_name is not None,
                "connected_device": self._connected_name,
                "available_devices": list(self._available),
                "last_error": self._last_error,
            }

    # Called from the MIDI library's thread: only queue, never run commands here.
    def _on_event(self, event: MidiEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass  # drop rather than block; faders send a new value right after

    def _dispatch(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._control.handle(event)
            except Exception:
                logger.exception("Unexpected failure while handling MIDI event %s", event)

    def _supervise(self) -> None:
        while not self._stop.is_set():
            self._scan()
            self._stop.wait(self._rescan)

    def _scan(self) -> None:
        try:
            names = self._ports.names()
        except Exception as error:
            self._set_error(f"MIDI system unavailable: {error}")
            self._disconnect()
            return
        with self._lock:
            self._available = names
            connected = self._connected_name
        if connected is not None and connected not in names:
            logger.warning("MIDI device '%s' disconnected.", connected)
            self._disconnect()
            connected = None
        if connected is not None:
            return
        match = next((n for n in names if self._device in n.lower()), None)
        if match is None:
            return
        try:
            port = self._ports.open(match, self._on_event)
        except Exception as error:
            self._set_error(f"Could not open MIDI device '{match}': {error}")
            return
        out: Optional[MidiOutputPort] = None
        try:
            out_name = next((n for n in self._ports.output_names() if self._device in n.lower()), None)
            if out_name is not None:
                out = self._ports.open_output(out_name)
        except Exception as error:
            logger.warning("MIDI output unavailable, no LED feedback: %s", error)
        with self._lock:
            self._port, self._connected_name, self._last_error = port, match, None
            self._out = out
            self._generation += 1
        logger.info("MIDI device '%s' connected.", match)
        if self._on_connect is not None:
            try:
                self._on_connect()
            except Exception:
                logger.exception("Setting up MIDI device '%s' failed", match)

    def _set_error(self, message: str) -> None:
        with self._lock:
            changed = message != self._last_error
            self._last_error = message
        if changed:
            logger.warning(message)

    def _disconnect(self) -> None:
        with self._lock:
            port, self._port, self._connected_name = self._port, None, None
            out, self._out = self._out, None
        if out is not None:
            try:
                out.close()
            except Exception as error:
                logger.warning("Closing MIDI output failed: %s", error)
        if port is not None:
            try:
                port.close()
            except Exception as error:
                logger.warning("Closing MIDI port failed: %s", error)
