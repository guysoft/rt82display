"""Process-based HID writer for the RT82 LCD (0x1919) device.

All I/O with the LCD device runs in a dedicated **subprocess** (fresh
Python interpreter).  The parent communicates via the child's
stdin/stdout pipes using length-prefixed pickle messages, with
``select``-based timeouts on every read.

Uses ``subprocess.Popen`` rather than ``os.fork`` because libusb (the
backend for hidapi on Linux) is **not fork-safe** — a forked child that
inherits the parent's libusb state will crash when opening a device.

If a ``dev.write()`` blocks in the kernel USB stack the child enters
uninterruptible sleep (state D).  The parent detects the timeout via
``select``, abandons the child (SIGKILL + WNOHANG), and closes the
pipes so the caller can proceed.
"""

import os
import pickle
import select
import signal
import struct
import subprocess
import sys
import time

# 4-byte big-endian length prefix for pipe messages.
_HDR = struct.Struct("!I")


def _send_msg(fd: int, obj: object) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    os.write(fd, _HDR.pack(len(data)) + data)


def _recv_msg(fd: int) -> object:
    hdr = b""
    while len(hdr) < _HDR.size:
        chunk = os.read(fd, _HDR.size - len(hdr))
        if not chunk:
            raise EOFError("pipe closed")
        hdr += chunk
    (length,) = _HDR.unpack(hdr)
    data = b""
    while len(data) < length:
        chunk = os.read(fd, length - len(data))
        if not chunk:
            raise EOFError("pipe closed mid-message")
        data += chunk
    return pickle.loads(data)


# ------------------------------------------------------------------
# Child entry point (invoked via  python -m rt82display.hid_writer)
# ------------------------------------------------------------------

def _child_main() -> None:
    """Subprocess entry: open device, process commands on stdin/stdout."""
    import hid

    device_path = sys.argv[1].encode()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()

    dev = hid.device()
    dev.open_path(device_path)
    dev.set_nonblocking(False)

    try:
        while True:
            cmd = _recv_msg(stdin_fd)
            if cmd is None:
                break
            packet, read_timeout_ms = cmd
            try:
                wr = dev.write(packet)
                try:
                    resp = dev.read(64, timeout_ms=read_timeout_ms)
                except Exception:
                    resp = None
                _send_msg(stdout_fd, ("ok", wr, resp))
            except Exception as e:
                _send_msg(stdout_fd, ("error", str(e), None))
    finally:
        try:
            dev.close()
        except Exception:
            pass


class HIDWriter:
    """Proxy that sends HID packets via a killable subprocess.

    Parameters
    ----------
    device_path : bytes
        The OS-level HID device path (from ``hid.enumerate()``).
    """

    def __init__(self, device_path: bytes):
        self.device_path = device_path
        self.proc: subprocess.Popen | None = None
        self._stdout_fd: int | None = None
        self._start()

    def _start(self):
        self.proc = subprocess.Popen(
            [
                sys.executable, "-m", "rt82display.hid_writer",
                self.device_path.decode(),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Detach from parent's stderr so child errors don't pollute
            # the parent's output; send to devnull instead.
            stderr=subprocess.DEVNULL,
        )
        self._stdout_fd = self.proc.stdout.fileno()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send65(
        self,
        pkt: list[int],
        read_timeout_ms: int = 1000,
        write_timeout_s: float = 3.0,
    ) -> tuple[bool, list | None]:
        """Send a 65-byte packet (report-ID 0x00 prefix) and read a response.

        Returns ``(success, response)`` where *success* is True when the
        underlying ``dev.write()`` returned >= 0.

        Raises ``TimeoutError`` if the worker doesn't respond within
        *write_timeout_s* seconds (e.g. the USB write is stuck in kernel
        state D).
        """
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError("HIDWriter not started")

        padded = pkt + [0] * (64 - len(pkt))
        packet = bytes([0x00] + padded)
        _send_msg(self.proc.stdin.fileno(), (packet, read_timeout_ms))

        ready, _, _ = select.select(
            [self._stdout_fd], [], [], write_timeout_s
        )
        if not ready:
            raise TimeoutError(
                f"HID write timed out after {write_timeout_s}s"
            )
        try:
            status, result, resp = _recv_msg(self._stdout_fd)
        except EOFError:
            raise TimeoutError("HID worker process died unexpectedly")

        if status == "error":
            raise IOError(result)
        return result >= 0, resp

    def close(self):
        """Graceful shutdown: ask the worker to quit, then clean up."""
        if self.proc is not None and self.proc.stdin is not None:
            try:
                _send_msg(self.proc.stdin.fileno(), None)
                self.proc.wait(timeout=2.0)
            except Exception:
                pass
        self.kill()

    def kill(self):
        """Best-effort kill of the worker.

        Sends SIGKILL and tries a non-blocking wait.  If the child is
        in kernel state D (uninterruptible USB sleep), it cannot be
        killed — we abandon it and close the pipes so the caller isn't
        blocked.
        """
        if self.proc is not None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            self.proc = None
        self._stdout_fd = None

    def restart(self):
        """Kill and respawn the worker (reopens the device)."""
        self.kill()
        time.sleep(0.3)
        self._start()


if __name__ == "__main__":
    _child_main()
