"""Command-line interface for RT82 Display tool.

Upload GIFs to your Epomaker RT82 keyboard's LCD screen.
"""

from pathlib import Path
import subprocess
import tempfile
import time

import click
import hid
from PIL import Image

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

# QGIF size limit (firmware buffer constraint)
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
    dev36.close()
    
    time.sleep(0.3)
    
    # Step 2: Connect to 1919 device
    dev = None
    for d in hid.enumerate(0x1919, 0x1919):
        if d.get('usage_page') == 0xFF:
            dev = hid.device()
            dev.open_path(d['path'])
            dev.set_nonblocking(True)
            break
    
    if not dev:
        raise ConnectionError("LCD interface (0x1919) did not appear after init")
    
    try:
        # Query and download mode
        muted("  Entering download mode...")
        send(dev, [0xAA, 0x10])
        send(dev, [0xAA, 0x17, 0,0,0,0x38,0,0, 2,0,2,6,0,2,3,0,4,0,9,1])
        send(dev, [0xAA, 0x11])
        send(dev, [0xAA, 0x1C])
        send(dev, [0xAA, 0x10])
        send(dev, [0xAA, 0x12, 0,0,0,0x38])
        send(dev, [0xAA, 0x11])
        send(dev, [0xAA, 0x1C])
        
        # Download mode trigger
        send(dev, [0xAA, 0x1B, 0,0,0,0x38])
        send(dev, [0xAA, 0xE3, 0,0,0,1,0,0,1], delay=0.1)
        send(dev, [0xAA, 0x14, 0,0,0,0x38])
        
        # Transfer setup
        muted("  Setting up transfer...")
        setup = [0xAA, 0x15, 0,0, 0,0x38, 0,0, 0, frame_count, fps, 0,0,
                 file_size & 0xFF, (file_size >> 8) & 0xFF, (file_size >> 16) & 0xFF]
        send(dev, setup)
        send(dev, [0xAA, 0x15, 0x38,0, 0,0x38])
        send(dev, [0xAA, 0x15, 0x70,0, 0,0x10])
        send(dev, [0xAA, 0x16, 0,0,0,0x38])
        send(dev, [0xAA, 0x18, 0,0,0,1,0,0, fps])
        
        # Data transfer
        CHUNK_SIZE = 56
        offset = 0
        packets_sent = 0
        total_packets = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        
        while offset < file_size:
            chunk = data[offset:offset + CHUNK_SIZE]
            if len(chunk) < CHUNK_SIZE:
                chunk = chunk + bytes(CHUNK_SIZE - len(chunk))
            
            pkt = [0xAA, 0x19, offset & 0xFF, (offset >> 8) & 0xFF, 0, 0x38, 0, 0] + list(chunk)
            send(dev, pkt, delay=0.001)
            
            offset += CHUNK_SIZE
            packets_sent += 1
            
            if packets_sent % 100 == 0 or packets_sent == total_packets:
                percent = (packets_sent / total_packets) * 100
                console.print(f"  [info]📦[/info] Packet {packets_sent}/{total_packets} ({percent:.0f}%)", end='\r')
        
        console.print()  # Clear line
        
        # Finalize
        muted("  Finalizing...")
        send(dev, [0xAA, 0x1C])
        send(dev, [0xAA, 0x1A, 0,0,0,0x38])
        
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
    
    Note: The RT82 has a ~64KB buffer limit. Simple GIFs with solid colors
    work best. Complex patterns may cause display artifacts.
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
        
        # Warn about size
        if len(data) > QGIF_SIZE_LIMIT:
            console.print()
            warning(f"File exceeds 64KB limit ({format_bytes(len(data))})")
            muted("  This may cause display artifacts (diagonal lines, garbage frames).")
            muted("  Try a simpler GIF with solid colors and minimal patterns.")
        
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
            
            # Warn about size
            if len(data) > QGIF_SIZE_LIMIT:
                console.print()
                warning(f"Output exceeds 64KB limit ({format_bytes(len(data))})")
                muted("  This may cause display artifacts.")
                muted("  Try a simpler GIF with solid colors and minimal patterns.")
                console.print()
                if not click.confirm("Upload anyway?", default=True):
                    raise click.Abort()
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
    Complex patterns may exceed the 64KB display limit.
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
        
        if file_size > QGIF_SIZE_LIMIT:
            console.print()
            warning(f"File exceeds 64KB limit!")
            muted("  This may cause display artifacts on the RT82.")
            muted("  Try a simpler GIF with solid colors.")
        
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
