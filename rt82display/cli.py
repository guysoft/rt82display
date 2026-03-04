"""Command-line interface for RT82 Display tool.

Upload GIFs to your Epomaker RT82 keyboard's LCD screen.
"""

import contextlib
import datetime
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Optional

import click
import hid
from PIL import Image, ImageOps

from .theme import (
    console, success, error, warning, info, muted,
    print_header, print_banner, step_progress, format_bytes
)
from . import __version__
from .image import load_gif, ProcessedGif, DISPLAY_WIDTH, DISPLAY_HEIGHT
from .hid_device import find_devices
from .hid_writer import HIDWriter
from .protocol import QGIF_MAGIC, is_qgif_data

# ---------------------------------------------------------------------------
# Device lock – prevents two processes from talking to the HID device at once
# ---------------------------------------------------------------------------

LOCK_PATH = Path(tempfile.gettempdir()) / "rt82display.lock"
UPLOAD_TIMEOUT = 120  # seconds; 0 disables the alarm


class DeviceBusy(Exception):
    """Raised when another process is already using the RT82 device."""


def _timeout_handler(signum, frame):
    raise TimeoutError(
        "Upload timed out – the device may be unresponsive. "
        "Try unplugging and re-plugging the keyboard."
    )


@contextlib.contextmanager
def rt82_device_lock():
    """Acquire an exclusive file lock so only one upload runs at a time."""
    lock_fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fd.close()
        raise DeviceBusy(
            "Another rt82display process is already uploading. "
            "If a service is running, wait for it to finish or stop it with: "
            "launchctl unload ~/Library/LaunchAgents/com.rt82weather.update.plist"
        )
    try:
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _find_native_encoder() -> Path | None:
    """Locate the native QGIF encoder binary.

    Search order:
      1. Bundled in the installed package (pip wheel with prebuilt binary)
      2. Adjacent to the source tree (dev / editable install)
      3. On $PATH (user built it and added to PATH)
    """
    import shutil
    import sys

    ext = ".exe" if sys.platform == "win32" else ""
    name = f"test_qgif{ext}"

    pkg_bin = Path(__file__).parent / "_bin" / name
    if pkg_bin.exists():
        return pkg_bin

    dev_path = Path(__file__).parent.parent / "wasm2c_runtime" / name
    if dev_path.exists():
        return dev_path

    found = shutil.which("test_qgif")
    if found:
        return Path(found)
    return None

NATIVE_ENCODER = _find_native_encoder()

# Default QGIF compression target (used by compress_gif_to_fit)
QGIF_SIZE_LIMIT = 65536  # 64KB


def gif_fps(gif_path: Path) -> int:
    """Extract FPS from a GIF's first frame delay (matching the web tool's formula).
    
    GIF delays are in centiseconds (1/100s). We use the first frame's delay
    and convert: fps = 1000 / delay_ms, clamped to 2-120.
    Default is 20fps if no delay info is found.
    """
    with Image.open(gif_path) as gif:
        delay_ms = gif.info.get('duration', 0)
    if delay_ms <= 0:
        return 20  # default, same as web tool
    return max(2, min(120, round(1000 / delay_ms)))


def encode_gif_to_qgif(gif_path: Path, output_path: Path, fps: int | None = None) -> bool:
    """
    Encode a GIF to QGIF using the native wasm2c encoder.
    
    Args:
        gif_path: Path to the input GIF
        output_path: Path for the output QGIF file
        fps: Frames per second (2-120). If None, auto-detected from GIF.
    
    Returns True on success.
    """
    WIDTH, HEIGHT = 240, 136  # Must be divisible by 4
    
    if NATIVE_ENCODER is None:
        raise FileNotFoundError(
            "Native QGIF encoder not found.\n"
            "  Build it:  git clone https://github.com/guysoft/rt82display && "
            "cd rt82display/wasm2c_runtime && ./build.sh\n"
            "  Then either add it to PATH or run from the repo directory."
        )
    
    # Auto-detect fps from GIF if not provided
    if fps is None:
        fps = gif_fps(gif_path)
    
    # Load GIF frames
    with Image.open(gif_path) as gif:
        frames = []
        try:
            while True:
                frame = gif.copy().convert('RGBA')
                frame = frame.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
                frames.append(frame)
                gif.seek(gif.tell() + 1)
        except EOFError:
            pass
    
    if not frames:
        raise ValueError("No frames found in GIF")
    
    # Save frames as PNG and encode
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        for i, frame in enumerate(frames):
            frame.save(tmpdir / f"input_{i}.png", 'PNG')
        
        input_pattern = str(tmpdir / "input_X.png")
        output_str = str(output_path.absolute())
        
        # Pass fps to native encoder (it auto-discovers frame count from files)
        result = subprocess.run(
            [str(NATIVE_ENCODER), input_pattern, output_str, str(fps)],
            capture_output=True,
            text=True
        )
        
        return result.returncode == 0


def encode_frames_to_qgif(frames: list[Image.Image], output_path: Path, fps: int = 20) -> bool:
    """
    Encode a list of PIL Image frames to QGIF using the native wasm2c encoder.
    
    Args:
        frames: List of PIL Image frames (will be resized to 240x136)
        output_path: Path for the output QGIF file
        fps: Frames per second (2-120, default 20)
    
    Returns True on success.
    """
    WIDTH, HEIGHT = 240, 136  # Must be divisible by 4
    
    if NATIVE_ENCODER is None:
        raise FileNotFoundError(
            "Native QGIF encoder not found.\n"
            "  Build it:  git clone https://github.com/guysoft/rt82display && "
            "cd rt82display/wasm2c_runtime && ./build.sh\n"
            "  Then either add it to PATH or run from the repo directory."
        )
    
    if not frames:
        raise ValueError("No frames provided")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        for i, frame in enumerate(frames):
            resized = frame.convert('RGBA').resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
            resized.save(tmpdir / f"input_{i}.png", 'PNG')
        
        input_pattern = str(tmpdir / "input_X.png")
        output_str = str(output_path.absolute())
        
        # Pass fps to native encoder (it auto-discovers frame count from files)
        result = subprocess.run(
            [str(NATIVE_ENCODER), input_pattern, output_str, str(fps)],
            capture_output=True,
            text=True
        )
        
        return result.returncode == 0


def compress_gif_to_fit(
    gif_path: Path,
    max_size: int = QGIF_SIZE_LIMIT,
) -> Optional[tuple[bytes, int, str]]:
    """
    Try progressively more aggressive compression until QGIF fits under max_size.
    
    Strategies (in order of aggressiveness):
      1. Posterize colors (fewer unique colors → better RLE compression)
      2. Drop frames (every 2nd, 3rd, etc.)
      3. Combinations of both
    
    Args:
        gif_path: Path to the input GIF
        max_size: Maximum output size in bytes (default 64KB)
    
    Returns:
        (qgif_data, frame_count, description) on success, or None if nothing works.
    """
    # Get the original FPS from the GIF
    original_fps = gif_fps(gif_path)
    
    # Load all frames from the GIF
    with Image.open(gif_path) as gif:
        all_frames = []
        try:
            while True:
                all_frames.append(gif.copy().convert('RGBA'))
                gif.seek(gif.tell() + 1)
        except EOFError:
            pass
    
    if not all_frames:
        return None
    
    total_frames = len(all_frames)
    
    # Posterize bits: 8 = no change, lower = fewer colors
    posterize_levels = [8, 6, 5, 4, 3]
    # Frame skip: 1 = keep all, 2 = every other, etc.
    max_skip = min(total_frames, 8)
    frame_skips = list(range(1, max_skip + 1))
    
    for skip in frame_skips:
        for bits in posterize_levels:
            # Select frames
            selected = all_frames[::skip]
            kept = len(selected)
            
            # When dropping frames, adjust fps to maintain original playback speed
            effective_fps = max(2, min(120, round(original_fps / skip)))
            
            # Apply posterization if needed
            if bits < 8:
                processed = [ImageOps.posterize(f.convert('RGB'), bits).convert('RGBA')
                             for f in selected]
            else:
                processed = selected
            
            # Encode to temp file
            with tempfile.NamedTemporaryFile(suffix='.qgif', delete=False) as tmp:
                tmp_path = Path(tmp.name)
            
            try:
                ok = encode_frames_to_qgif(processed, tmp_path, fps=effective_fps)
                if not ok:
                    continue
                
                qgif_data = bytearray(tmp_path.read_bytes())
                
                # Patch header for compatibility
                if len(qgif_data) > 5 and qgif_data[5] == 0x05:
                    qgif_data[5] = 0x03
                
                size = len(qgif_data)
                
                if size <= max_size:
                    # Build description of what changed
                    changes = []
                    if skip > 1:
                        changes.append(f"{kept}/{total_frames} frames")
                    if bits < 8:
                        colors = 2 ** bits
                        changes.append(f"{colors} colors/channel")
                    desc = ", ".join(changes) if changes else "no changes needed"
                    
                    return bytes(qgif_data), kept, desc
                
                # Show progress
                muted(f"  trying {kept} frames, {2**bits if bits < 8 else 'full'} colors → {format_bytes(size)}")
            finally:
                tmp_path.unlink(missing_ok=True)
    
    return None


def patch_qgif_header(qgif_path: Path) -> int:
    """
    Patch QGIF header byte 5 from 0x05 to 0x03 for display compatibility.
    
    Returns the file size after patching.
    """
    data = bytearray(qgif_path.read_bytes())
    
    if len(data) < 10:
        raise ValueError("QGIF file too small")
    
    # Patch byte 5 if needed
    if data[5] == 0x05:
        data[5] = 0x03
        qgif_path.write_bytes(data)
    
    return len(data)


def _find_lcd_path() -> bytes | None:
    """Return the HID device path for the 0x1919 LCD, or None.

    Prefer usage_page 0xFF (macOS); fall back to IF=1 then first (Linux).
    On Linux (libusb backend), usage_page is 0 for all interfaces.
    """
    lcd_devices = list(hid.enumerate(0x1919, 0x1919))
    for d in lcd_devices:
        if d.get("usage_page") == 0xFF:
            return d["path"]
    for d in lcd_devices:
        if d.get("interface_number") == 1:
            return d["path"]
    if lcd_devices:
        return lcd_devices[0]["path"]
    return None


def _open_kbd_device() -> hid.device:
    """Find and open the 0x36B0 keyboard raw-HID interface.

    On macOS usage_page is reported correctly, so we match 0xFF60.
    On Linux (libusb backend) usage_page is 0 for all interfaces, so
    we fall back to interface_number == 1 (the standard QMK raw HID
    interface).

    Raises ConnectionError if not found.
    """
    kbd_devices = list(hid.enumerate(0x36B0, 0x30A3))
    target_kbd = None
    # Priority 1: exact usage_page match
    for d in kbd_devices:
        if d.get("usage_page") == 0xFF60:
            target_kbd = d
            break
    # Priority 2: interface 1 (QMK raw HID convention)
    if target_kbd is None:
        for d in kbd_devices:
            if d.get("interface_number") == 1:
                target_kbd = d
                break
    # Priority 3: any device with this VID:PID
    if target_kbd is None and kbd_devices:
        target_kbd = kbd_devices[0]
    if target_kbd is None:
        raise ConnectionError("Could not find keyboard interface (0x36B0)")
    dev = hid.device()
    dev.open_path(target_kbd["path"])
    dev.set_nonblocking(True)
    return dev


def _send_kbd(dev, pkt, delay=0.02):
    """Send a 64-byte packet on the 0x36B0 keyboard device."""
    packet = bytes(pkt + [0] * (64 - len(pkt)))
    result = dev.write(packet)
    time.sleep(delay)
    return result >= 0


def _find_usb_sysfs_path(vid: int, pid: int) -> str | None:
    """Find the sysfs device path for a USB device by VID:PID."""
    usb_devices = "/sys/bus/usb/devices"
    try:
        for entry in os.listdir(usb_devices):
            dev_dir = os.path.join(usb_devices, entry)
            try:
                with open(os.path.join(dev_dir, "idVendor")) as f:
                    dev_vid = f.read().strip()
                with open(os.path.join(dev_dir, "idProduct")) as f:
                    dev_pid = f.read().strip()
                if dev_vid == f"{vid:04x}" and dev_pid == f"{pid:04x}":
                    return entry
            except (FileNotFoundError, PermissionError):
                continue
    except FileNotFoundError:
        pass
    return None


def _usb_reset_device(sysfs_name: str) -> bool:
    """Reset a USB device by unbinding and rebinding its driver.

    This is equivalent to physically unplugging and replugging.
    Works even when the device is stuck (kernel state D on URB wait)
    because unbind operates on the driver binding, not the device I/O.

    Returns True on success.
    """
    unbind = "/sys/bus/usb/drivers/usb/unbind"
    bind = "/sys/bus/usb/drivers/usb/bind"
    try:
        with open(unbind, "w") as f:
            f.write(sysfs_name)
        time.sleep(1.0)
        with open(bind, "w") as f:
            f.write(sysfs_name)
        return True
    except PermissionError:
        return False
    except OSError:
        return False


def _try_reset_lcd(lcd_path: bytes) -> bool:
    """Try to reset a stuck LCD by sending End + Start via a short-lived worker.

    If the device is responsive, this sends the protocol-level reset
    commands.  If the write blocks (device stuck), the worker is
    abandoned and we return False.
    """
    writer = None
    try:
        writer = HIDWriter(lcd_path)
        writer.send65([0xAA, 0x1A], write_timeout_s=2.0)  # End
        writer.send65([0xAA, 0x11], write_timeout_s=2.0)  # Start
        return True
    except (TimeoutError, Exception):
        return False
    finally:
        if writer is not None:
            writer.kill()


def _kill_stuck_rt82_processes() -> bool:
    """Find and kill any stuck rt82weather/rt82display upload processes.

    Uses /proc on Linux to avoid a psutil dependency.  Returns True if
    any process was signalled.  Note: processes stuck in kernel state D
    (uninterruptible USB sleep) won't actually die until the USB
    transfer completes or the device is reset.
    """
    my_pid = os.getpid()
    killed_any = False

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == my_pid:
            continue
        try:
            cmdline_path = f"/proc/{pid}/cmdline"
            with open(cmdline_path, "rb") as f:
                raw = f.read()
            cmdline = raw.replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            )
            if "rt82" in cmdline and (
                "upload" in cmdline or "update" in cmdline
            ):
                muted(
                    f"  Killing stuck process {pid} "
                    f"({cmdline.strip()[:60]})"
                )
                os.kill(pid, signal.SIGKILL)
                killed_any = True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue

    return killed_any


def reset_display() -> bool:
    """Software-reset the RT82 display without unplugging.

    Strategy:
      1. Kill any stuck upload/update processes.
      2. Try protocol-level reset (End + Start) on the LCD device.
         If the device is stuck in kernel state D this will time out
         quickly without blocking the caller.
      3. If protocol reset fails, try USB unbind/rebind (requires write
         access to sysfs — works with appropriate udev rules).
      4. Re-init via the keyboard device.

    Returns True on success.
    """
    _kill_stuck_rt82_processes()

    lcd_path = _find_lcd_path()
    if lcd_path:
        muted("  LCD device found, attempting protocol reset...")
        if _try_reset_lcd(lcd_path):
            muted("  Protocol reset succeeded")
        else:
            warning(
                "  Protocol reset failed (device unresponsive) "
                "— trying USB reset..."
            )
            sysfs = _find_usb_sysfs_path(0x1919, 0x1919)
            if sysfs and _usb_reset_device(sysfs):
                muted("  USB reset succeeded")
                time.sleep(2.0)
            else:
                warning(
                    "  USB reset not available (needs permission). "
                    "Unplug and replug the keyboard."
                )
                return False

    try:
        dev36 = _open_kbd_device()
        _send_kbd(dev36, [0xAA, 0xE2])
        for _ in range(5):
            _send_kbd(dev36, [0xAA, 0xE0])
        dev36.close()
        muted("  Keyboard re-initialized")
    except ConnectionError:
        pass

    return True


def _send65_retry(
    writer: HIDWriter,
    pkt: list[int],
    max_retries: int = 3,
    write_timeout: float = 3.0,
) -> tuple[bool, list | None]:
    """Send a packet via the HIDWriter, retrying on timeout.

    On timeout the worker is killed and respawned (reopening the device).
    """
    for attempt in range(max_retries):
        try:
            return writer.send65(pkt, write_timeout_s=write_timeout)
        except TimeoutError:
            if attempt < max_retries - 1:
                warning(
                    f"  Write timeout, restarting connection "
                    f"({attempt + 2}/{max_retries})..."
                )
                writer.restart()
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
    raise TimeoutError("HID write failed after all retries")


def upload_to_device(data: bytes, frame_count: int = 1) -> bool:
    """Upload data to RT82 using the two-step protocol.

    Acquires an exclusive file lock so two processes (e.g. the launchd
    service and a manual invocation) cannot upload simultaneously.

    Returns True on success, raises Exception on failure.
    """
    with rt82_device_lock():
        old_alarm = signal.signal(signal.SIGALRM, _timeout_handler)
        if UPLOAD_TIMEOUT:
            signal.alarm(UPLOAD_TIMEOUT)
        try:
            return _upload_to_device_locked(data, frame_count)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_alarm)


def _upload_to_device_locked(data: bytes, frame_count: int = 1) -> bool:
    """Inner upload logic — must be called while holding rt82_device_lock.

    Uses a worker process for all LCD (0x1919) I/O so that a blocked
    ``hid_write()`` can be killed and retried without hanging the caller.
    """
    file_size = len(data)
    writer: HIDWriter | None = None
    dev36: hid.device | None = None

    # ------------------------------------------------------------------
    # Recovery preamble: if the LCD device is already present from a
    # previous stuck session, try to reset it before starting fresh.
    # ------------------------------------------------------------------
    stale_lcd = _find_lcd_path()
    if stale_lcd:
        warning("  LCD device already present — cleaning up stale session...")
        if _try_reset_lcd(stale_lcd):
            muted("  Protocol reset succeeded")
            time.sleep(0.5)
        else:
            warning("  Protocol reset failed, trying USB reset...")
            sysfs = _find_usb_sysfs_path(0x1919, 0x1919)
            if sysfs and _usb_reset_device(sysfs):
                muted("  USB reset succeeded")
                time.sleep(2.0)
            elif _find_lcd_path() is not None:
                raise ConnectionError(
                    "LCD device is stuck from a previous session and "
                    "could not be reset.\n"
                    "  Please unplug and replug the keyboard, then retry."
                )

    try:
        # --------------------------------------------------------------
        # Step 1: Init on 0x36B0 to activate the 0x1919 LCD device
        # --------------------------------------------------------------
        info("Initializing display...")
        dev36 = _open_kbd_device()

        _send_kbd(dev36, [0xAA, 0xE2])
        for _ in range(5):
            _send_kbd(dev36, [0xAA, 0xE0])

        # --------------------------------------------------------------
        # Step 2: Wait for 0x1919 LCD device to appear.
        # On macOS this is near-instant (~300ms), but on Linux the USB
        # re-enumeration through the libusb backend can take 1-2 s.
        # --------------------------------------------------------------
        lcd_path = None
        for _attempt in range(10):
            time.sleep(0.3)
            lcd_path = _find_lcd_path()
            if lcd_path is not None:
                break

        if lcd_path is None:
            raise ConnectionError(
                "LCD interface (0x1919) did not appear after init"
            )

        # --------------------------------------------------------------
        # Step 3: Open the LCD device via a killable worker process
        # --------------------------------------------------------------
        writer = HIDWriter(lcd_path)

        # LCD init sequence on 0x1919
        muted("  Entering download mode...")
        writer.send65([0xAA, 0x10])
        writer.send65(
            [0xAA, 0x17, 0, 0, 0, 0x38, 0, 0,
             2, 0, 2, 6, 0, 2, 3, 0, 4, 0, 9, 1]
        )
        writer.send65([0xAA, 0x11])
        writer.send65([0xAA, 0x1C])
        writer.send65([0xAA, 0x10])
        writer.send65([0xAA, 0x12, 0, 0, 0, 0x38])
        writer.send65([0xAA, 0x11])
        writer.send65([0xAA, 0x1C])

        # Send AA E3 (getGifCount) on KEYBOARD device (0x36B0).
        # The web tool sends this on the keyboard's 32-byte interface,
        # with both devices open, right before starting the transfer.
        _send_kbd(dev36, [0xAA, 0xE3, 0, 0, 0, 1, 0, 0, 1])  # 1 screen
        dev36.close()
        dev36 = None

        # Prepare and screen prep on LCD
        writer.send65([0xAA, 0x1B, 0, 0, 0, 0x38])
        writer.send65([0xAA, 0xE3, 0, 0, 0, 1, 0, 0, 1])
        writer.send65([0xAA, 0x14, 0, 0, 0, 0x38])

        # --------------------------------------------------------------
        # Transfer setup (matching web tool exactly)
        # --------------------------------------------------------------
        muted("  Setting up transfer...")
        erase_count = (file_size + 65535) // 65536 + 1
        setup = [
            0xAA, 0x15, 0, 0, 0, 0x38, 0, 0,
            0, 1, erase_count, 0, 0,
            file_size & 0xFF,
            (file_size >> 8) & 0xFF,
            (file_size >> 16) & 0xFF,
        ]
        writer.send65(setup)
        writer.send65([0xAA, 0x15, 0x38, 0, 0, 0x38])
        writer.send65([0xAA, 0x15, 0x70, 0, 0, 0x10])
        writer.send65([0xAA, 0x16, 0, 0, 0, 0x38])
        writer.send65([0xAA, 0x18, 0, 0, 0, 1, 0, 0, erase_count])

        # --------------------------------------------------------------
        # Wait for flash erase: poll the device instead of a fixed sleep
        # --------------------------------------------------------------
        muted(
            f"  Erasing {erase_count} blocks "
            f"({format_bytes(file_size)})..."
        )
        erase_deadline = time.time() + max(2.0, 1.0 * erase_count)
        device_ready = False
        while time.time() < erase_deadline:
            time.sleep(0.3)
            try:
                ok, resp = writer.send65(
                    [0xAA, 0x1C], write_timeout_s=1.0
                )
                if ok and resp:
                    device_ready = True
                    break
            except TimeoutError:
                continue
        if not device_ready:
            time.sleep(1.0)

        # --------------------------------------------------------------
        # Data transfer with write-then-read flow control
        # Uses _send65_retry to recover from transient hangs.
        # --------------------------------------------------------------
        CHUNK_SIZE = 56
        offset = 0
        packets_sent = 0
        total_packets = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        start_time = time.time()

        while offset < file_size:
            chunk = data[offset:offset + CHUNK_SIZE]
            chunk_len = len(chunk)
            if chunk_len < CHUNK_SIZE:
                chunk = chunk + bytes(CHUNK_SIZE - chunk_len)

            pkt = [
                0xAA, 0x19,
                offset & 0xFF,
                (offset >> 8) & 0xFF,
                (offset >> 16) & 0xFF,
                chunk_len, 0, 0,
            ] + list(chunk)
            _send65_retry(writer, pkt)

            offset += CHUNK_SIZE
            packets_sent += 1

            if packets_sent % 200 == 0 or packets_sent == total_packets:
                elapsed = time.time() - start_time
                rate = packets_sent / elapsed if elapsed > 0 else 0
                percent = (packets_sent / total_packets) * 100
                console.print(
                    f"  [info]📦[/info] {packets_sent}/{total_packets} "
                    f"({percent:.0f}%) [{rate:.0f} pkt/s]",
                    end="\r",
                )

        console.print()  # clear progress line

        # --------------------------------------------------------------
        # Finalize (matching web tool: End → syncTime → Start)
        # --------------------------------------------------------------
        muted("  Finalizing...")
        writer.send65([0xAA, 0x1A])  # End (clean, no extra bytes)
        # syncTime: Query → Time config → Start (matching web tool)
        writer.send65([0xAA, 0x10])  # Query

        now = datetime.datetime.now()
        yr = now.year
        sync = [0xAA, 0x17, 0, 0, 0, 0x38, 0, 0]
        sync.extend([
            (yr // 1000) % 10, (yr // 100) % 10,
            (yr // 10) % 10, yr % 10,
            (now.month // 10) % 10, now.month % 10,
            now.weekday() % 7,
            (now.day // 10) % 10, now.day % 10,
            (now.hour // 10) % 10, now.hour % 10,
            (now.minute // 10) % 10, now.minute % 10,
            (now.second // 10) % 10, now.second % 10,
        ])
        writer.send65(sync)
        writer.send65([0xAA, 0x11])  # Start

        return True

    except (KeyboardInterrupt, SystemExit):
        warning("  Interrupted — attempting to reset display...")
        if writer is not None:
            try:
                writer.send65([0xAA, 0x1A], write_timeout_s=1.0)
                writer.send65([0xAA, 0x11], write_timeout_s=1.0)
            except Exception:
                pass
        raise

    finally:
        if writer is not None:
            writer.kill()
        if dev36 is not None:
            try:
                dev36.close()
            except Exception:
                pass


@click.group()
@click.version_option(version=__version__, prog_name="rt82display")
def main():
    """RT82 Display - Upload GIFs to your Epomaker RT82 keyboard screen."""
    pass


@main.command()
@click.argument('file_path', type=click.Path(exists=True, path_type=Path))
@click.option('--raw', is_flag=True, help='Send raw RGB565 (experimental, likely garbled)')
def upload(file_path: Path, raw: bool):
    """Upload a GIF or QGIF file to the RT82 display.
    
    FILE_PATH is the path to the file to upload.
    
    Supports:
      - .qgif files (pre-compressed)
      - .gif files (auto-encoded with native QGIF encoder)
    
    Supports files larger than 64KB using 24-bit offset addressing.
    Simple GIFs with solid colors compress best.
    """
    print_banner()
    console.print()
    
    # Check file type
    is_qgif_file = file_path.suffix.lower() == '.qgif'
    is_gif_file = file_path.suffix.lower() == '.gif'
    
    if is_qgif_file:
        # Load pre-compressed QGIF
        print_header("Loading QGIF", "📦")
        qgif_data = bytearray(file_path.read_bytes())
        
        if not is_qgif_data(bytes(qgif_data)):
            error("File does not have valid QGIF header (expected 'QGIF' magic)")
            raise click.Abort()
        
        # Auto-patch byte 5 if needed
        if len(qgif_data) > 5 and qgif_data[5] == 0x05:
            qgif_data[5] = 0x03
            muted("  🔧 Patched header for compatibility")
        
        muted(f"  📄 {file_path.name}")
        muted(f"  💾 {format_bytes(len(qgif_data))}")
        
        # Extract frame info from header
        frame_count = qgif_data[7] if len(qgif_data) > 7 else 1
        data = bytes(qgif_data)
        
    elif is_gif_file and not raw:
        # Encode GIF using native encoder
        print_header("Encoding GIF", "🎬")
        
        try:
            gif = load_gif(file_path)
        except Exception as e:
            error(f"Failed to load GIF: {e}")
            raise click.Abort()
        
        # Auto-detect FPS from GIF frame delay (matches web tool behavior)
        detected_fps = gif_fps(file_path)
        
        muted(f"  📄 {file_path.name}")
        muted(f"  📐 {gif.width}x{gif.height} → 240x136")
        muted(f"  🎞️  {gif.total_frames} frames @ {detected_fps} fps")
        
        # Check if native encoder is available
        if NATIVE_ENCODER is None:
            console.print()
            warning("Native QGIF encoder not found!")
            muted("  To upload GIFs directly, build the encoder:")
            muted("    git clone https://github.com/guysoft/rt82display")
            muted("    cd rt82display/wasm2c_runtime && ./build.sh")
            muted("  Then add wasm2c_runtime/ to your PATH, or run from the repo.")
            console.print()
            muted("  Alternative: use the web tool https://image.rdmctmzt.com/")
            muted("  to create a .qgif file, then upload that.")
            raise click.Abort()
        
        # Encode to temporary QGIF
        with tempfile.NamedTemporaryFile(suffix='.qgif', delete=False) as tmp:
            tmp_path = Path(tmp.name)
        
        try:
            muted(f"  ⚙️  Compressing with native encoder...")
            
            if not encode_gif_to_qgif(file_path, tmp_path, fps=detected_fps):
                error("QGIF encoding failed")
                raise click.Abort()
            
            # Read and patch the QGIF
            qgif_data = bytearray(tmp_path.read_bytes())
            
            if len(qgif_data) > 5 and qgif_data[5] == 0x05:
                qgif_data[5] = 0x03
            
            frame_count = gif.total_frames
            data = bytes(qgif_data)
            
            muted(f"  ✅ Encoded: {format_bytes(len(data))}")
        finally:
            tmp_path.unlink(missing_ok=True)
        
    else:
        error("Raw RGB565 mode is not supported")
        raise click.Abort()
    
    console.print()
    print_header("Uploading", "📤")
    
    try:
        upload_to_device(data, frame_count=frame_count)
        console.print()
        success("Upload complete!")
            
    except ConnectionError as e:
        console.print()
        error(f"Connection failed: {e}")
        raise click.Abort()
    except Exception as e:
        console.print()
        error(f"Upload failed: {e}")
        raise click.Abort()


@main.command(name='list')
def list_devices():
    """List connected RT82 devices."""
    print_banner()
    console.print()
    
    print_header("Devices", "🔍")
    
    devices = find_devices()
    
    if not devices:
        warning("No RT82 devices found")
        console.print()
        muted("  Make sure your keyboard is connected via USB.")
        return
    
    for i, device in enumerate(devices, 1):
        console.print(f"  [info]{i}.[/info] [highlight]{device.product_name}[/highlight]")
        muted(f"     VID:PID  {device.vid_pid}")
        muted(f"     Usage Page: 0x{device.usage_page:04X}  Interface: {device.interface_number}")
        muted(f"     Manufacturer: {device.manufacturer}")
        if device.serial_number:
            muted(f"     Serial: {device.serial_number}")
    
    console.print()
    info(f"Found {len(devices)} device{'s' if len(devices) > 1 else ''}")


@main.command()
def info_cmd():
    """Show information about the display and protocol."""
    print_banner()
    console.print()
    
    print_header("Display Info", "📺")
    
    console.print(f"  [tertiary]Resolution:[/tertiary] {DISPLAY_WIDTH}x{DISPLAY_HEIGHT} pixels")
    console.print(f"  [tertiary]Color Format:[/tertiary] QGIF (compressed)")
    console.print(f"  [tertiary]Bytes per frame:[/tertiary] {DISPLAY_WIDTH * DISPLAY_HEIGHT * 2:,} (uncompressed)")
    console.print(f"  [tertiary]Max Screens:[/tertiary] 3")
    
    console.print()
    print_header("Device Info", "🔌")
    
    console.print(f"  [tertiary]Keyboard VID:PID:[/tertiary] 36B0:30A3 (init device)")
    console.print(f"  [tertiary]LCD VID:PID:[/tertiary] 1919:1919 (data transfer)")
    console.print(f"  [tertiary]Packet Size:[/tertiary] 64 bytes")
    
    console.print()
    print_header("Upload Workflow", "📋")
    
    muted("  1. rt82display connects to 0x36B0 and sends init")
    muted("  2. 0x1919 LCD device appears")
    muted("  3. Download mode triggered (screen shows 'Downloading')")
    muted("  4. QGIF data transferred in 56-byte chunks")
    muted("  5. Transfer finalized, image displays")
    
    console.print()
    print_header("QGIF Format", "🎬")
    
    warning("RT82 requires QGIF compressed format!")
    console.print()
    muted("  To upload images:")
    muted("    1. Go to https://image.rdmctmzt.com/")
    muted("    2. Upload your GIF")
    muted("    3. Use browser console to capture QGIF data")
    muted("    4. Upload the .qgif file with: rt82display upload file.qgif")


@main.command()
@click.argument('gif_path', type=click.Path(exists=True, path_type=Path))
@click.argument('output_path', type=click.Path(path_type=Path))
def encode(gif_path: Path, output_path: Path):
    """Encode a GIF file to QGIF format.
    
    Uses the native wasm2c encoder (same code as web tool).
    
    Note: Simple GIFs with solid colors compress best.
    """
    print_banner()
    console.print()
    
    print_header("Encoding to QGIF", "🎬")
    
    # Check native encoder
    if NATIVE_ENCODER is None:
        error("Native QGIF encoder not found!")
        muted("  Build it:  git clone https://github.com/guysoft/rt82display")
        muted("             cd rt82display/wasm2c_runtime && ./build.sh")
        muted("  Then add wasm2c_runtime/ to your PATH, or run from the repo.")
        raise click.Abort()
    
    try:
        gif = load_gif(gif_path)
    except Exception as e:
        error(f"Failed to load GIF: {e}")
        raise click.Abort()
    
    detected_fps = gif_fps(gif_path)
    
    muted(f"  📄 {gif_path.name}")
    muted(f"  📐 {gif.width}x{gif.height} → 240x136")
    muted(f"  🎞️  {gif.total_frames} frames @ {detected_fps} fps")
    
    muted("  ⚙️  Compressing...")
    
    try:
        if not encode_gif_to_qgif(gif_path, output_path, fps=detected_fps):
            error("Encoding failed")
            raise click.Abort()
        
        # Patch header
        file_size = patch_qgif_header(output_path)
        
        console.print()
        success(f"Saved: {output_path}")
        muted(f"  💾 {format_bytes(file_size)}")
        
    except Exception as e:
        error(f"Failed: {e}")
        raise click.Abort()


@main.command()
@click.argument('gif_path', type=click.Path(exists=True, path_type=Path))
@click.argument('output_path', type=click.Path(path_type=Path))
def convert(gif_path: Path, output_path: Path):
    """Convert a GIF to raw RGB565 data (for debugging)."""
    print_banner()
    console.print()
    
    print_header("Converting", "🔄")
    
    try:
        gif = load_gif(gif_path)
    except Exception as e:
        error(f"Failed to load GIF: {e}")
        raise click.Abort()
    
    info(f"Loaded: {gif_path.name}")
    muted(f"  {gif.total_frames} frames, {gif.width}x{gif.height}")
    
    # Write raw RGB565 data
    with open(output_path, 'wb') as f:
        for frame in gif.frames:
            f.write(frame.data)
    
    console.print()
    success(f"Saved RGB565 data to: {output_path}")
    muted(f"  Total size: {format_bytes(gif.total_size)}")


@main.command(name='reset')
def reset_cmd():
    """Reset the RT82 display (recover from a stuck upload).

    Kills any stuck upload process, sends reset commands to the LCD,
    and re-initializes the keyboard device -- no unplug needed.
    """
    print_banner()
    console.print()

    print_header("Resetting Display", "🔄")

    try:
        reset_display()
        console.print()
        success("Display reset complete")
    except Exception as e:
        console.print()
        error(f"Reset failed: {e}")
        muted("  You may need to unplug and replug the keyboard.")
        raise click.Abort()


# Alias for 'info' command
main.add_command(info_cmd, name='info')


if __name__ == '__main__':
    main()
