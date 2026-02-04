"""QGIF Encoder - Generate QGIF files for RT82 keyboard displays.

Based on reverse-engineered specification:
- 32-byte global header
- 8-byte frame headers  
- RLE (PackBits) compressed RGB565 data
- Little Endian byte order
"""

import struct
from pathlib import Path
from typing import List, Tuple
from PIL import Image

# Display dimensions
DISPLAY_WIDTH = 240
DISPLAY_HEIGHT = 135


def rgba_to_rgb565(r: int, g: int, b: int, a: int = 255) -> int:
    """Convert 8-bit RGBA to 16-bit RGB565."""
    # Flatten alpha (transparent → black)
    if a < 128:
        return 0x0000
    
    # Quantize channels
    r5 = (r >> 3) & 0x1F  # 5 bits
    g6 = (g >> 2) & 0x3F  # 6 bits  
    b5 = (b >> 3) & 0x1F  # 5 bits
    
    # Pack into 16 bits
    return (r5 << 11) | (g6 << 5) | b5


def rle_compress(pixels: List[int]) -> bytes:
    """Compress RGB565 pixel array using PackBits RLE.
    
    Control byte format:
    - MSB=1: Run of (N & 0x7F) + 1 identical pixels
    - MSB=0: Literal sequence of N + 1 pixels
    """
    result = bytearray()
    i = 0
    n = len(pixels)
    
    while i < n:
        # Look for runs (3+ identical pixels)
        run_length = 1
        while (i + run_length < n and 
               run_length < 128 and
               pixels[i + run_length] == pixels[i]):
            run_length += 1
        
        if run_length >= 3:
            # Encode as run: control byte with MSB set
            result.append(0x80 | (run_length - 1))
            pixel = pixels[i]
            result.append(pixel & 0xFF)         # Low byte
            result.append((pixel >> 8) & 0xFF)  # High byte
            i += run_length
        else:
            # Encode as literal sequence
            literal_start = i
            literal_count = 0
            
            while i < n and literal_count < 128:
                # Check if next 3 pixels would start a run
                if (i + 2 < n and
                    pixels[i] == pixels[i + 1] == pixels[i + 2]):
                    break
                literal_count += 1
                i += 1
            
            if literal_count > 0:
                result.append(literal_count - 1)  # MSB = 0
                for j in range(literal_start, literal_start + literal_count):
                    pixel = pixels[j]
                    result.append(pixel & 0xFF)
                    result.append((pixel >> 8) & 0xFF)
    
    return bytes(result)


def rle_decompress(data: bytes, pixel_count: int) -> List[int]:
    """Decompress RLE data back to RGB565 pixels (for testing)."""
    pixels = []
    i = 0
    
    while i < len(data) and len(pixels) < pixel_count:
        control = data[i]
        i += 1
        
        if control & 0x80:  # Run
            count = (control & 0x7F) + 1
            if i + 1 < len(data):
                pixel = data[i] | (data[i + 1] << 8)
                i += 2
                pixels.extend([pixel] * count)
        else:  # Literal
            count = control + 1
            for _ in range(count):
                if i + 1 < len(data):
                    pixel = data[i] | (data[i + 1] << 8)
                    i += 2
                    pixels.append(pixel)
    
    return pixels


def encode_frame(image: Image.Image) -> Tuple[bytes, List[int]]:
    """Convert PIL Image to RLE-compressed RGB565 data.
    
    Returns (compressed_data, raw_pixels for debugging).
    """
    # Ensure correct size and mode
    if image.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        image = image.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
    
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    
    # Convert to RGB565
    pixels = []
    for y in range(DISPLAY_HEIGHT):
        for x in range(DISPLAY_WIDTH):
            r, g, b, a = image.getpixel((x, y))
            rgb565 = rgba_to_rgb565(r, g, b, a)
            pixels.append(rgb565)
    
    # Compress
    compressed = rle_compress(pixels)
    
    return compressed, pixels


def encode_qgif(gif_path: Path, output_path: Path = None,
                width: int = DISPLAY_WIDTH, 
                height: int = DISPLAY_HEIGHT) -> bytes:
    """Encode a GIF file to QGIF format.
    
    Args:
        gif_path: Path to input GIF file
        output_path: Optional path to save QGIF file
        width: Target width (default 240)
        height: Target height (default 135)
    
    Returns:
        QGIF data as bytes
    """
    gif = Image.open(gif_path)
    
    frames_data = []
    delays = []
    
    # Extract and encode all frames
    try:
        frame_idx = 0
        while True:
            # Get frame
            frame = gif.copy()
            frame = frame.convert('RGBA')
            frame = frame.resize((width, height), Image.LANCZOS)
            
            # Encode
            compressed, _ = encode_frame(frame)
            frames_data.append(compressed)
            
            # Get delay (in ms)
            delay = gif.info.get('duration', 100)
            delays.append(delay)
            
            frame_idx += 1
            gif.seek(gif.tell() + 1)
    except EOFError:
        pass
    
    # Build QGIF file
    result = bytearray()
    
    # Calculate total data size (frame headers + frame data)
    total_data_size = sum(8 + len(fd) for fd in frames_data)
    
    # Global Header (32 bytes)
    result.extend(b'QGIF')                                    # Magic
    result.extend(struct.pack('<H', 0x0100))                  # Version
    result.extend(struct.pack('<H', width))                   # Width
    result.extend(struct.pack('<H', height))                  # Height
    result.extend(struct.pack('<H', len(frames_data)))        # Frame count
    result.extend(struct.pack('<H', delays[0] if delays else 100))  # Global delay
    result.extend(struct.pack('<H', 0x0000))                  # Reserved/CRC
    result.extend(struct.pack('<I', total_data_size))         # Total data size
    result.extend(b'\x00' * 12)                               # Padding to 32 bytes
    
    # Frame Blocks
    for i, (frame_data, delay) in enumerate(zip(frames_data, delays)):
        # Frame Header (8 bytes)
        result.extend(struct.pack('<H', len(frame_data)))     # Frame size
        result.extend(struct.pack('<H', delay))               # Frame delay
        result.extend(struct.pack('<H', 0x0001))              # Flags (keyframe)
        result.extend(struct.pack('<H', 0x0000))              # Reserved
        
        # Frame Body
        result.extend(frame_data)
    
    # EOF Marker
    result.extend(b'\xFF\xFF\xFF\xFF')
    
    qgif_data = bytes(result)
    
    # Save if output path provided
    if output_path:
        output_path.write_bytes(qgif_data)
    
    return qgif_data


def encode_single_image(image_path: Path, output_path: Path = None,
                        width: int = DISPLAY_WIDTH,
                        height: int = DISPLAY_HEIGHT) -> bytes:
    """Encode a single image (PNG/JPG) to QGIF format."""
    image = Image.open(image_path)
    image = image.convert('RGBA')
    image = image.resize((width, height), Image.LANCZOS)
    
    compressed, _ = encode_frame(image)
    
    # Build QGIF with single frame
    result = bytearray()
    
    total_data_size = 8 + len(compressed)
    
    # Global Header
    result.extend(b'QGIF')
    result.extend(struct.pack('<H', 0x0100))
    result.extend(struct.pack('<H', width))
    result.extend(struct.pack('<H', height))
    result.extend(struct.pack('<H', 1))            # 1 frame
    result.extend(struct.pack('<H', 0))            # No delay for static
    result.extend(struct.pack('<H', 0x0000))
    result.extend(struct.pack('<I', total_data_size))
    result.extend(b'\x00' * 12)
    
    # Single Frame
    result.extend(struct.pack('<H', len(compressed)))
    result.extend(struct.pack('<H', 0))
    result.extend(struct.pack('<H', 0x0001))
    result.extend(struct.pack('<H', 0x0000))
    result.extend(compressed)
    
    # EOF
    result.extend(b'\xFF\xFF\xFF\xFF')
    
    qgif_data = bytes(result)
    
    if output_path:
        output_path.write_bytes(qgif_data)
    
    return qgif_data


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python -m rt82display.qgif <input.gif> [output.qgif]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else input_path.with_suffix('.qgif')
    
    print(f"Encoding {input_path} → {output_path}")
    data = encode_qgif(input_path, output_path)
    print(f"Done! Output size: {len(data):,} bytes")
