"""Tests for upload device-lock and timeout behaviour.

These tests verify that:
 - upload_to_device acquires a file lock so two processes cannot upload
   simultaneously (the root cause of the "Erasing blocks" hang).
 - The lock is released after a successful or failed upload.
 - An overall timeout prevents indefinite hangs.
"""

import fcntl
import signal
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _try_lock_nonblocking(lock_path: Path) -> bool:
    """Return True if we can acquire LOCK_EX|LOCK_NB on *lock_path*."""
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        fd.close()


def _build_mock_hid(monkeypatch):
    """Patch hid.enumerate / hid.device so upload_to_device never touches
    real hardware.  Returns (mock_dev36, mock_dev_lcd)."""
    mock_dev36 = MagicMock()
    mock_dev36.write.return_value = 64
    mock_dev36.read.return_value = [0] * 64

    mock_dev_lcd = MagicMock()
    mock_dev_lcd.write.return_value = 65
    mock_dev_lcd.read.return_value = [0] * 64

    kbd_entry = {
        "vendor_id": 0x36B0, "product_id": 0x30A3,
        "usage_page": 0xFF60, "interface_number": 1,
        "path": b"/dev/fake_kbd",
    }
    lcd_entry = {
        "vendor_id": 0x1919, "product_id": 0x1919,
        "usage_page": 0xFF, "interface_number": 1,
        "path": b"/dev/fake_lcd",
    }

    def fake_enumerate(vid=0, pid=0):
        if vid == 0x36B0:
            return [kbd_entry]
        if vid == 0x1919:
            return [lcd_entry]
        return []

    opened = {"count": 0}
    def fake_device():
        dev = MagicMock()
        if opened["count"] == 0:
            dev.write = mock_dev36.write
            dev.read = mock_dev36.read
        else:
            dev.write = mock_dev_lcd.write
            dev.read = mock_dev_lcd.read
        opened["count"] += 1
        return dev

    monkeypatch.setattr("hid.enumerate", fake_enumerate)
    monkeypatch.setattr("hid.device", fake_device)
    return mock_dev36, mock_dev_lcd


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUploadDeviceLock:
    """upload_to_device must acquire an exclusive file lock."""

    def test_upload_raises_device_busy_when_locked(self, tmp_path, monkeypatch):
        """If the lock is already held, upload_to_device must raise
        DeviceBusy *before* touching any HID device."""
        from rt82display.cli import upload_to_device, DeviceBusy, LOCK_PATH

        lock_path = tmp_path / "rt82display.lock"
        monkeypatch.setattr("rt82display.cli.LOCK_PATH", lock_path)

        # Simulate another process holding the lock.
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            with pytest.raises(DeviceBusy):
                upload_to_device(b"\x00" * 100, frame_count=1)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def test_lock_released_after_successful_upload(self, tmp_path, monkeypatch):
        """After a normal upload the lock file must be released so the
        next invocation can proceed."""
        from rt82display.cli import upload_to_device, LOCK_PATH

        lock_path = tmp_path / "rt82display.lock"
        monkeypatch.setattr("rt82display.cli.LOCK_PATH", lock_path)
        _build_mock_hid(monkeypatch)

        qgif_stub = b"QGIF" + b"\x00" * 200
        upload_to_device(qgif_stub, frame_count=1)

        assert _try_lock_nonblocking(lock_path), "Lock was not released after upload"

    def test_lock_released_on_failure(self, tmp_path, monkeypatch):
        """If the upload fails mid-way the lock must still be released."""
        from rt82display.cli import upload_to_device, LOCK_PATH

        lock_path = tmp_path / "rt82display.lock"
        monkeypatch.setattr("rt82display.cli.LOCK_PATH", lock_path)

        # Make hid.enumerate return nothing → ConnectionError
        monkeypatch.setattr("hid.enumerate", lambda *a, **kw: [])

        with pytest.raises(ConnectionError):
            upload_to_device(b"\x00" * 100, frame_count=1)

        assert _try_lock_nonblocking(lock_path), "Lock was not released after failure"


class TestUploadTimeout:
    """upload_to_device must not hang indefinitely."""

    def test_upload_times_out_on_hung_device(self, tmp_path, monkeypatch):
        """If the device stops responding, the upload should be killed by
        the SIGALRM-based timeout rather than blocking forever."""
        from rt82display.cli import upload_to_device, LOCK_PATH

        lock_path = tmp_path / "rt82display.lock"
        monkeypatch.setattr("rt82display.cli.LOCK_PATH", lock_path)
        monkeypatch.setattr("rt82display.cli.UPLOAD_TIMEOUT", 2)

        hang_event = threading.Event()

        _, mock_lcd = _build_mock_hid(monkeypatch)
        original_write = mock_lcd.write

        call_count = {"n": 0}
        def blocking_write(pkt):
            call_count["n"] += 1
            if call_count["n"] > 5:
                hang_event.wait(30)
            return 65

        mock_lcd.write.side_effect = blocking_write

        try:
            with pytest.raises(TimeoutError):
                upload_to_device(b"QGIF" + b"\x00" * 2000, frame_count=1)
        finally:
            hang_event.set()
