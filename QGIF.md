# QGIF Format Specification

Comprehensive documentation of the proprietary QGIF format used by Epomaker, Womier, Gamakay, and other RDMCTMZT-platform keyboards with LCD displays.

## Overview

**QGIF** is a proprietary compressed image/animation format designed for embedded LCD displays on mechanical keyboards. The name likely derives from "Qt-processed GIF" - referencing the Qt Framework toolchain used to create it.

### Why QGIF Exists

Standard GIFs require:
- **Decompression buffer**: Significant RAM for LZW dictionary
- **Color conversion**: CPU cycles for palette lookup → RGB565
- **Frame buffer**: Full frame in RAM for disposal methods

Typical keyboard MCUs have <64KB SRAM. A single 240×135 RGB565 frame = 64.8KB, exhausting all memory. QGIF pre-processes everything on the host, creating ready-to-stream data for the MCU.

## Hardware Ecosystem

### Manufacturer
- **ODM**: Shaodong Tongchi Technology Development Co., Ltd.
- **Platform Code**: RDMCTMZT
- **USB VID**: `0x36B0`

### Supported Devices
| Device | Resolution | Notes |
|--------|------------|-------|
| Epomaker RT82 | 240×135 | Detachable 1.14" LCD |
| Womier RD75/SK80 | 240×135 | Integrated display |
| Gamakay SK80 | 240×135 | Rebrand of SK80 |
| Royal Kludge (some) | Various | Shares VID 0x36B0 |

### Display Controller
- **Chip**: Sitronix ST7789V or GalaxyCore GC9A01
- **Interface**: 4-Wire SPI
- **Color Depth**: 16-bit RGB565
- **Byte Order**: Little Endian (LSB first)

## File Format Specification

**Byte Order**: Little Endian (consistent with ARM Cortex-M architecture)

### Global Header (32 bytes)

| Offset | Size | Field | Type | Description |
|--------|------|-------|------|-------------|
| 0x00 | 4 | Magic | char[4] | `"QGIF"` (0x51 0x47 0x49 0x46) |
| 0x04 | 2 | Version | uint16 | 0x0100 = v1.0. May indicate compression type |
| 0x06 | 2 | Width | uint16 | Frame width in pixels (e.g., 240) |
| 0x08 | 2 | Height | uint16 | Frame height in pixels (e.g., 135) |
| 0x0A | 2 | Frame Count | uint16 | Total number of frames |
| 0x0C | 2 | Global Delay | uint16 | Default frame delay in milliseconds |
| 0x0E | 2 | Reserved/CRC | uint16 | Padding or CRC16 checksum |
| 0x10 | 4 | Total Data Size | uint32 | Size of payload (excluding header) |
| 0x14 | 12 | Padding | bytes | Reserved (alignment to 32 bytes) |

### Frame Block Structure

Each frame consists of a **Frame Header** followed by **Frame Body**.

#### Frame Header (8 bytes)

| Offset | Size | Field | Type | Description |
|--------|------|-------|------|-------------|
| 0x00 | 2 | Frame Size | uint16 | Size of compressed frame body |
| 0x02 | 2 | Frame Delay | uint16 | Override for this frame (ms) |
| 0x04 | 2 | Flags | uint16 | Bit 0: Keyframe, Bit 1: Delta frame |
| 0x06 | 2 | Reserved | uint16 | Alignment padding |

#### Frame Body

Contains RLE-compressed RGB565 pixel data (see Compression section).

### End of File Marker

```
0xFF 0xFF 0xFF 0xFF
```
Sentinel value to prevent buffer overruns if header is corrupted.

## Color Space: RGB565

QGIF uses 16-bit RGB565 color format (not RGB888 or indexed palette).

### Bit Layout
```
15 14 13 12 11 | 10 9 8 7 6 5 | 4 3 2 1 0
R  R  R  R  R  | G  G  G  G  G  G | B  B  B  B  B
   (5 bits)    |    (6 bits)      |   (5 bits)
```

### Conversion Formula

```python
def rgba_to_rgb565(r, g, b, a=255):
    """Convert 8-bit RGBA to 16-bit RGB565."""
    # Flatten alpha (transparent → black)
    if a < 128:
        return 0x0000
    
    # Quantize channels
    r5 = (r >> 3) & 0x1F  # 5 bits
    g6 = (g >> 2) & 0x3F  # 6 bits  
    b5 = (b >> 3) & 0x1F  # 5 bits
    
    # Pack into 16 bits
    rgb565 = (r5 << 11) | (g6 << 5) | b5
    return rgb565
```

### Byte Order (Little Endian)
```python
# For writing to file:
byte_0 = rgb565 & 0xFF        # Low byte (Blue end)
byte_1 = (rgb565 >> 8) & 0xFF # High byte (Red end)
```

**Visual Diagnostic**: If colors look "psychedelic" (red↔blue swapped), byte order is wrong.

## Compression: RLE (PackBits Variant)

QGIF uses Run-Length Encoding optimized for the vector-style graphics common on keyboard displays.

### Algorithm

The stream consists of **Control Bytes** followed by **Data Bytes**:

```
1. Read Control Byte (N)
2. Check MSB (bit 7):
   
   If MSB = 1 (Run):
     - Count = (N & 0x7F) + 1
     - Read next 2 bytes (one RGB565 pixel)
     - Output that pixel Count times
   
   If MSB = 0 (Literal):
     - Count = N + 1
     - Read next (Count × 2) bytes
     - Output as raw pixels
```

### Efficiency

| Scenario | Uncompressed | RLE Compressed |
|----------|--------------|----------------|
| Solid color (240×135) | 64,800 bytes | ~640 bytes |
| High-frequency noise | 64,800 bytes | ~65,300 bytes |

### Implementation

```python
def rle_compress(pixels_rgb565):
    """Compress RGB565 pixel array using PackBits RLE."""
    result = bytearray()
    i = 0
    
    while i < len(pixels_rgb565):
        # Look for runs
        run_length = 1
        while (i + run_length < len(pixels_rgb565) and 
               run_length < 128 and
               pixels_rgb565[i + run_length] == pixels_rgb565[i]):
            run_length += 1
        
        if run_length >= 3:
            # Encode as run: control byte with MSB set
            result.append(0x80 | (run_length - 1))
            pixel = pixels_rgb565[i]
            result.append(pixel & 0xFF)
            result.append((pixel >> 8) & 0xFF)
            i += run_length
        else:
            # Encode as literal sequence
            literal_start = i
            literal_count = 0
            
            while (i < len(pixels_rgb565) and 
                   literal_count < 128):
                # Check if next would start a run
                if (i + 2 < len(pixels_rgb565) and
                    pixels_rgb565[i] == pixels_rgb565[i+1] == pixels_rgb565[i+2]):
                    break
                literal_count += 1
                i += 1
            
            if literal_count > 0:
                result.append(literal_count - 1)  # MSB = 0
                for j in range(literal_start, literal_start + literal_count):
                    pixel = pixels_rgb565[j]
                    result.append(pixel & 0xFF)
                    result.append((pixel >> 8) & 0xFF)
    
    return bytes(result)
```

## Delta Encoding (Advanced)

Some firmware versions support differential encoding for better compression:

1. **Frame 1**: Full keyframe (RLE compressed)
2. **Frame 2+**: Compare with previous frame
   - Calculate bounding box of changed pixels
   - Set Delta flag in Frame Header
   - Add 4 bytes: X_Start, Y_Start, Width, Height
   - Only compress pixels within bounding box

## Storage Constraints

### SPI Flash Budget
- **Total Capacity**: 4MB - 8MB (W25Q32/W25Q64)
- **Firmware Overhead**: ~512KB
- **Available for Animations**: ~2-3MB

### Size Calculation
```
Raw 100-frame animation:
240 × 135 × 2 bytes × 100 frames = 6.48 MB (exceeds capacity!)

With RLE compression:
Typically 5-10x smaller → 0.6 - 1.3 MB (fits!)
```

**Implementation Note**: Generator should fail/warn if output exceeds ~2MB.

## The Qt Connection

### Why "QGIF"?
- `qgif.dll` is a standard Qt Framework plugin for reading GIF files
- The ODM developers used Qt to build their configuration software
- They named their custom format "QGIF" (Qt-processed GIF)
- The `51 47 49 46` header is their custom magic, NOT a Qt serialization

### Workflow
1. User imports `animation.gif` into Qt-based software
2. Software uses `qgif.dll` to decode GIF → RGBA frames
3. Software processes frames (resize, rotate)
4. Software encodes to proprietary RLE format (this spec)
5. Output named "QGIF" after the Qt plugin that read the source

**Important**: Do NOT use Qt's GIF writer. Write custom binary encoder.

## Complete QGIF Generator

```python
import struct
from PIL import Image

def generate_qgif(gif_path, output_path, target_width=240, target_height=135):
    """Generate QGIF file from standard GIF."""
    
    # Load and process GIF
    gif = Image.open(gif_path)
    frames = []
    delays = []
    
    try:
        while True:
            # Resize frame
            frame = gif.copy()
            frame = frame.convert('RGBA')
            frame = frame.resize((target_width, target_height), Image.LANCZOS)
            
            # Convert to RGB565
            pixels = []
            for y in range(target_height):
                for x in range(target_width):
                    r, g, b, a = frame.getpixel((x, y))
                    rgb565 = rgba_to_rgb565(r, g, b, a)
                    pixels.append(rgb565)
            
            # RLE compress
            compressed = rle_compress(pixels)
            frames.append(compressed)
            
            # Get delay
            delay = gif.info.get('duration', 100)
            delays.append(delay)
            
            gif.seek(gif.tell() + 1)
    except EOFError:
        pass
    
    # Build QGIF file
    with open(output_path, 'wb') as f:
        # Global Header (32 bytes)
        total_data_size = sum(8 + len(frame) for frame in frames)
        
        f.write(b'QGIF')                              # Magic
        f.write(struct.pack('<H', 0x0100))            # Version
        f.write(struct.pack('<H', target_width))      # Width
        f.write(struct.pack('<H', target_height))     # Height
        f.write(struct.pack('<H', len(frames)))       # Frame count
        f.write(struct.pack('<H', delays[0] if delays else 100))  # Global delay
        f.write(struct.pack('<H', 0x0000))            # Reserved
        f.write(struct.pack('<I', total_data_size))   # Total data size
        f.write(b'\x00' * 12)                         # Padding
        
        # Frame Blocks
        for i, (frame_data, delay) in enumerate(zip(frames, delays)):
            # Frame Header (8 bytes)
            f.write(struct.pack('<H', len(frame_data)))  # Frame size
            f.write(struct.pack('<H', delay))            # Frame delay
            f.write(struct.pack('<H', 0x0001))           # Flags (keyframe)
            f.write(struct.pack('<H', 0x0000))           # Reserved
            
            # Frame Body
            f.write(frame_data)
        
        # EOF Marker
        f.write(b'\xFF\xFF\xFF\xFF')
    
    return output_path
```

## USB Transport Protocol

### Packet Structure (64 bytes)
```
Byte 0:    Report ID (0x00)
Byte 1:    Command
Byte 2-3:  Sequence Index (uint16 LE)
Byte 4-63: 60 bytes payload
```

### Commands
| Command | Byte | Description |
|---------|------|-------------|
| START_UPLOAD | 0x20 | Erases SPI Flash sector |
| DATA_PACKET | 0x22 | 60-byte data chunk |
| END_UPLOAD | 0x23 | Validates and activates |

### Transfer Flow
1. Send `START_UPLOAD` → Wait for ACK (flash erase: 500ms-2s)
2. Loop: Send `DATA_PACKET` with 60-byte chunks
3. Send `END_UPLOAD` with checksum

## Validation

### Visual Diagnostics
| Symptom | Cause | Fix |
|---------|-------|-----|
| Psychedelic colors | Byte order wrong | Swap RGB565 bytes |
| Skewed/wrapped image | Wrong dimensions | Resize to 240×135 |
| Partial display | Size mismatch in header | Update Total Data Size |
| Ghosting | Delta frames on non-supporting FW | Use keyframes only |

### Testing
- Intercept WebHID traffic from `image.rdmctmzt.com` with Wireshark/USBPcap
- Compare generated QGIF bytes with captured data
- Verify header fields match

## WASM Decompilation Findings

Using [WABT (WebAssembly Binary Toolkit)](https://github.com/WebAssembly/wabt), we decompiled `qgif.wasm` to understand the actual encoding algorithm.

### Key Discoveries

#### 1. Magic Number
```c
// At offset 8877 in decompiled code:
e[102]:int@1 = 1179207505;  // = 0x46494751 = "QGIF" (little-endian)
```

#### 2. RGB565 Conversion (Actual Formula)
The WASM uses floating-point conversion with specific coefficients:

```c
// From decompiled code (lines 9290-9300):
r = i32_trunc_sat_f64_u(f64_convert_i32_u(d[0]:ubyte) * 0.121569 + 0.5) << 11;
g = i32_trunc_sat_f64_u(f64_convert_i32_u(d[1]:ubyte) * 0.247059 + 0.5) << 5;
b = i32_trunc_sat_f64_u(f64_convert_i32_u(d[2]:ubyte) * 0.121569 + 0.5);
rgb565 = r | g | b;
```

**Coefficients explained:**
- `0.121569 ≈ 31/255` - Maps 0-255 → 0-31 (5-bit)
- `0.247059 ≈ 63/255` - Maps 0-255 → 0-63 (6-bit)
- `+ 0.5` - Rounding to nearest integer

#### 3. Dimension Requirements
```c
// Error message in data section:
"Error!The width and height must be divisible by 4."
```
The encoder requires dimensions divisible by 4 for block-based processing.

#### 4. Block-Based Processing
The encoder processes pixels in 4×4 blocks, comparing against a threshold (`v`) to determine compression strategy:

```c
// Threshold selection based on flags:
var v:int = select_if(32, 128, qa = c & 2);
```

#### 5. Compression Modes
The encoder has multiple output modes based on pixel similarity:
- **Mode 1**: All 16 pixels identical → 8-byte block
- **Mode 2**: 2-color dithering → 12-byte block  
- **Mode 3**: Full literal → 48-byte block (3 bytes × 16 pixels)

### Files Generated

| File | Size | Description |
|------|------|-------------|
| `qgif.wat` | 1.3 MB | WebAssembly Text format (readable) |
| `qgif_decompiled.dcmp` | 299 KB | Pseudo-C decompilation |

### Decompilation Commands

```bash
# Install WABT
brew install wabt  # macOS
# or: apt install wabt  # Linux

# Generate WebAssembly Text format
wasm2wat qgif.wasm -o qgif.wat

# Generate pseudo-C decompilation (more readable)
wasm-decompile qgif.wasm -o qgif_decompiled.dcmp

# View exports
wasm-objdump -x qgif.wasm
```

### Exported Functions

| Export | Signature | Description |
|--------|-----------|-------------|
| `m` (compress_video_wasm) | `(string, string, int, int) → int` | Main compression function |
| `k` | `(int) → void` | Memory free |
| `l` | `(int) → int` | Memory allocate |
| `j` | `() → void` | Initialization |

## Files in Repository

| File | Description |
|------|-------------|
| `qgif.wasm` | Original WebAssembly encoder (79KB) |
| `qgif.js` | Emscripten JS wrapper |
| `qgif.wat` | Decompiled WAT format (1.3MB) |
| `qgif_decompiled.dcmp` | Pseudo-C decompilation (299KB) |
| `qgif_encoder.js` | Node.js bridge (WIP) |
| `PROTOCOL.md` | USB HID protocol documentation |

## References

- [WABT - WebAssembly Binary Toolkit](https://github.com/WebAssembly/wabt)
- Shaodong Tongchi Technology (ODM)
- Qt Framework `qgif.dll` plugin
- ST7789V Display Controller Datasheet
- LVGL Embedded Graphics Library (similar RLE implementation)
