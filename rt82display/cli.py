"""Command-line interface for RT82 Display tool.

Upload GIFs to your Epomaker RT82 keyboard's LCD screen.
"""

from pathlib import Path
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
from .image import load_gif, ProcessedGif, DISPLAY_WIDTH, DISPLAY_HEIGHT
from .hid_device import find_devices
from .protocol import QGIF_MAGIC, is_qgif_data

__version__ = "0.1.0"

# Path to native encoder
NATIVE_ENCODER = Path(__file__).parent.parent / "wasm2c_runtime" / "test_qgif"

# Default QGIF compression target (used by compress_gif_to_fit)
QGIF_SIZE_LIMIT = 65536  # 64KB


def encode_gif_to_qgif(gif_path: Path, output_path: Path) -> bool:
    """
    Encode a GIF to QGIF using the native wasm2c encoder.
    
    Returns True on success.
    """
    WIDTH, HEIGHT = 240, 136  # Must be divisible by 4
    
    if not NATIVE_ENCODER.exists():
        raise FileNotFoundError(
            f"Native encoder not built. Run:\n"
            f"  cd {NATIVE_ENCODER.parent} && ./build.sh"
        )
    
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
        
        result = subprocess.run(
            [str(NATIVE_ENCODER), input_pattern, output_str, str(len(frames))],
            capture_output=True,
            text=True
        )
        
        return result.returncode == 0


def encode_frames_to_qgif(frames: list[Image.Image], output_path: Path) -> bool:
    """
    Encode a list of PIL Image frames to QGIF using the native wasm2c encoder.
    
    Args:
        frames: List of PIL Image frames (will be resized to 240x136)
        output_path: Path for the output QGIF file
    
    Returns True on success.
    """
    WIDTH, HEIGHT = 240, 136  # Must be divisible by 4
    
    if not NATIVE_ENCODER.exists():
        raise FileNotFoundError(
            f"Native encoder not built. Run:\n"
            f"  cd {NATIVE_ENCODER.parent} && ./build.sh"
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
        
        result = subprocess.run(
            [str(NATIVE_ENCODER), input_pattern, output_str, str(len(frames))],
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
                ok = encode_frames_to_qgif(processed, tmp_path)
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


def upload_to_device(data: bytes, frame_count: int = 1, fps: int = 8) -> bool:
    """Upload data to RT82 using the two-step protocol.
    
    Returns True on success, raises Exception on failure.
    """
    file_size = len(data)
    
    def send(dev, pkt, delay=0.02):
        packet = bytes(pkt + [0] * (64 - len(pkt)))
        result = dev.write(packet)
        time.sleep(delay)
        return result >= 0
    
    def send65(dev, pkt, delay=0.0):
        """Send a 65-byte packet (report ID 0x00 prefix) to the 0x1919 device.
        
        The web tool sends 65-byte HID reports:
          - byte[0] = 0x00 (report ID)
          - bytes[1..64] = packet data (0xAA command + payload)
        And reads a response after every write for flow control.
        """
        padded = pkt + [0] * (64 - len(pkt))
        packet = bytes([0x00] + padded)  # 65 bytes: report ID 0x00 + data
        result = dev.write(packet)
        # Read response for flow control (like the web tool does)
        try:
            resp = dev.read(64, timeout_ms=500)
        except Exception:
            resp = None
        if delay > 0:
            time.sleep(delay)
        return result >= 0
    
    # Step 1: Init on 36B0 to activate 1919 device
    info("Initializing display...")
    dev36 = None
    for d in hid.enumerate(0x36B0, 0x30A3):
        if d.get('usage_page') == 0xFF60:
            dev36 = hid.device()
            dev36.open_path(d['path'])
            dev36.set_nonblocking(True)
            break
    
    if not dev36:
        raise ConnectionError("Could not find keyboard interface (0x36B0)")
    
    send(dev36, [0xAA, 0xE2])
    for _ in range(5):
        send(dev36, [0xAA, 0xE0])
    # Keep dev36 open - we need it later for AA E3
    
    time.sleep(0.3)
    
    # Step 2: Connect to 1919 device
    dev = None
    for d in hid.enumerate(0x1919, 0x1919):
        if d.get('usage_page') == 0xFF:
            dev = hid.device()
            dev.open_path(d['path'])
            dev.set_nonblocking(False)  # Blocking reads for flow control
            break
    
    if not dev:
        dev36.close()
        raise ConnectionError("LCD interface (0x1919) did not appear after init")
    
    try:
        # LCD init sequence on 0x1919
        muted("  Entering download mode...")
        send65(dev, [0xAA, 0x10])
        send65(dev, [0xAA, 0x17, 0,0,0,0x38,0,0, 2,0,2,6,0,2,3,0,4,0,9,1])
        send65(dev, [0xAA, 0x11])
        send65(dev, [0xAA, 0x1C])
        send65(dev, [0xAA, 0x10])
        send65(dev, [0xAA, 0x12, 0,0,0,0x38])
        send65(dev, [0xAA, 0x11])
        send65(dev, [0xAA, 0x1C])
        
        # Send AA E3 (getGifCount) on KEYBOARD device (0x36B0).
        # The web tool sends this on the keyboard's 32-byte interface,
        # with both devices open, right before starting the transfer.
        send(dev36, [0xAA, 0xE3, 0,0,0,1,0,0, 1])  # 1 screen
        dev36.close()
        
        # Prepare and screen prep on LCD
        send65(dev, [0xAA, 0x1B, 0,0,0,0x38])
        send65(dev, [0xAA, 0xE3, 0,0,0,1,0,0,1])
        send65(dev, [0xAA, 0x14, 0,0,0,0x38])
        
        # Transfer setup (matching web tool exactly)
        muted("  Setting up transfer...")
        erase_count = (file_size + 65535) // 65536 + 1
        setup = [0xAA, 0x15, 0,0, 0,0x38, 0,0,
                 0, 1, erase_count, 0, 0,
                 file_size & 0xFF, (file_size >> 8) & 0xFF, (file_size >> 16) & 0xFF]
        send65(dev, setup)
        send65(dev, [0xAA, 0x15, 0x38,0, 0,0x38])
        send65(dev, [0xAA, 0x15, 0x70,0, 0,0x10])
        send65(dev, [0xAA, 0x16, 0,0,0,0x38])
        send65(dev, [0xAA, 0x18, 0,0,0,1,0,0, erase_count])
        muted(f"  Erasing {erase_count} blocks ({format_bytes(file_size)})...")
        time.sleep(0.5 * erase_count)  # Wait for flash erase (500ms per 64KB block)
        
        # Data transfer with write-then-read flow control
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
            
            pkt = [0xAA, 0x19,
                   offset & 0xFF, (offset >> 8) & 0xFF, (offset >> 16) & 0xFF,
                   chunk_len, 0, 0] + list(chunk)
            send65(dev, pkt)
            
            offset += CHUNK_SIZE
            packets_sent += 1
            
            if packets_sent % 200 == 0 or packets_sent == total_packets:
                elapsed = time.time() - start_time
                rate = packets_sent / elapsed if elapsed > 0 else 0
                percent = (packets_sent / total_packets) * 100
                console.print(f"  [info]📦[/info] {packets_sent}/{total_packets} ({percent:.0f}%) [{rate:.0f} pkt/s]", end='\r')
        
        console.print()  # Clear line
        
        # Finalize (matching web tool exactly: End → syncTime → Start)
        muted("  Finalizing...")
        send65(dev, [0xAA, 0x1A])  # End (clean, no extra bytes)
        # syncTime: Query → Time config → Start (matching web tool)
        import datetime
        send65(dev, [0xAA, 0x10])  # Query
        sync = [0xAA, 0x17, 0,0,0,0x38,0,0]
        now = datetime.datetime.now()
        yr = now.year
        sync.extend([
            (yr // 1000) % 10, (yr // 100) % 10, (yr // 10) % 10, yr % 10,
            ((now.month) // 10) % 10, (now.month) % 10,
            now.weekday() % 7,
            (now.day // 10) % 10, now.day % 10,
            (now.hour // 10) % 10, now.hour % 10,
            (now.minute // 10) % 10, now.minute % 10,
            (now.second // 10) % 10, now.second % 10
        ])
        send65(dev, sync)
        send65(dev, [0xAA, 0x11])  # Start
        
        return True
        
    finally:
        dev.close()


@click.group()
@click.version_option(version=__version__, prog_name="rt82display")
def main():
    """RT82 Display - Upload GIFs to your Epomaker RT82 keyboard screen."""
    pass


@main.command()
@click.argument('file_path', type=click.Path(exists=True, path_type=Path))
@click.option('--raw', is_flag=True, help='Send raw RGB565 (experimental, likely garbled)')
@click.option('--fps', default=8, help='Frames per second (default: 8)')
def upload(file_path: Path, raw: bool, fps: int):
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
        
        muted(f"  📄 {file_path.name}")
        muted(f"  📐 {gif.width}x{gif.height} → 240x136")
        muted(f"  🎞️  {gif.total_frames} frames")
        
        # Check if native encoder is available
        if not NATIVE_ENCODER.exists():
            console.print()
            warning("Native QGIF encoder not built!")
            muted(f"  Build it with: cd {NATIVE_ENCODER.parent} && ./build.sh")
            console.print()
            muted("  Alternative: use the web tool https://image.rdmctmzt.com/")
            muted("  to create a .qgif file, then upload that.")
            raise click.Abort()
        
        # Encode to temporary QGIF
        with tempfile.NamedTemporaryFile(suffix='.qgif', delete=False) as tmp:
            tmp_path = Path(tmp.name)
        
        try:
            muted("  ⚙️  Compressing with native encoder...")
            
            if not encode_gif_to_qgif(file_path, tmp_path):
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
        upload_to_device(data, frame_count=frame_count, fps=fps)
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
    if not NATIVE_ENCODER.exists():
        error("Native encoder not built!")
        muted(f"  Run: cd {NATIVE_ENCODER.parent} && ./build.sh")
        raise click.Abort()
    
    try:
        gif = load_gif(gif_path)
    except Exception as e:
        error(f"Failed to load GIF: {e}")
        raise click.Abort()
    
    muted(f"  📄 {gif_path.name}")
    muted(f"  📐 {gif.width}x{gif.height} → 240x136")
    muted(f"  🎞️  {gif.total_frames} frames")
    
    muted("  ⚙️  Compressing...")
    
    try:
        if not encode_gif_to_qgif(gif_path, output_path):
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


# Alias for 'info' command
main.add_command(info_cmd, name='info')


if __name__ == '__main__':
    main()
