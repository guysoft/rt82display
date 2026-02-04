# Epomaker RT82 LCD Screen Protocol

This document describes the reverse-engineered protocol for communicating with the Epomaker RT82 keyboard's LCD screen.

## Device Identification

The RT82 exposes **two** USB composite devices:

| Property | LCD Interface ✅ | Keyboard Interface |
|----------|------------------|---------------------|
| Vendor ID | `0x1919` (6425) | `0x36B0` (14000) |
| Product ID | `0x1919` (6425) | `0x30A3` (12451) |
| Product Name | Epomaker RT82 | Epomaker RT82 |
| Manufacturer | RDR | RDMCTMZT |
| Usage Page | `0xFF` (255) | Various |
| Interface | 1 | 0, 1, 2 |

### ⚠️ Critical: Two-Step Device Activation

The `0x1919:0x1919` device **does NOT appear** until you send init commands to the `0x36B0:0x30A3` device first!

**Working Sequence:**
1. Connect to `0x36B0:0x30A3` (Usage Page `0xFF60`, Interface 1)
2. Send init packets: `AA E2`, then `AA E0` x5
3. Wait ~300ms
4. **Now** `0x1919:0x1919` device appears
5. Connect to `0x1919:0x1919` (Usage Page `0xFF`, Interface 1)
6. Continue with download mode and data transfer

### USB Interfaces

**LCD Device (0x1919:0x1919) - appears after init:**
| Interface | Usage Page | Purpose |
|-----------|------------|---------|
| 0 | 0x0001 | Mouse HID |
| 1 | **0x00FF** | **LCD Communication** ✅ |

**Keyboard Device (0x36B0:0x30A3) - always visible:**
| Interface | Usage Page | Purpose |
|-----------|------------|---------|
| 0 | 0x0001 | Keyboard HID |
| 1 | **0xFF60** | **Init device** (send init here) |
| 2 | 0x0001 | Consumer/Media |

## Display Specifications

- **Resolution:** 240×135 pixels
- **Color Format:** QGIF (proprietary compressed format)
- **Bytes per frame (uncompressed RGB565):** 64,800 (240 × 135 × 2)
- **Max Screens:** 3 (the RT82 supports up to 3 virtual screens)

## HID Communication

- **Report ID:** 0 (don't prepend to packets)
- **Packet Size:** 64 bytes
- **Magic Byte:** `0xAA` (170)

### Command Reference

All commands start with the magic byte `0xAA` followed by a command byte.

| Command | Byte | Description |
|---------|------|-------------|
| Init | `0xE2` | Initialize device |
| Handshake | `0xE0` | Ready/handshake signal |
| Query | `0x10` | Query device info |
| Screen Query | `0x12` | Query screen info |
| Config | `0x17` | Configuration packet |
| Start | `0x11` | Begin operation |
| Status | `0x1C` | Status/heartbeat |
| Prepare | `0x1B` | Prepare for download |
| **Download Mode** | `0xE3` | **Enter download mode** (screen shows "Downloading") |
| Screen Prep | `0x14` | Prepare screen for data |
| Transfer Setup | `0x15` | Setup transfer (includes file size) |
| Transfer Ready | `0x16` | Ready to receive data |
| Frame Info | `0x18` | Set frame count and FPS |
| **Data** | `0x19` | **Data packet with QGIF chunk** |
| End | `0x1A` | End transfer |

## Complete Upload Sequence (VERIFIED WORKING)

This sequence triggers "Downloading" on the screen and uploads data:

### Phase 1: Init (on 0x36B0 device)
```python
# Connect to 0x36B0:0x30A3, Usage Page 0xFF60
send([0xAA, 0xE2] + [0]*62)  # Init
for _ in range(5):
    send([0xAA, 0xE0] + [0]*62)  # Handshake x5
# Wait 300ms, then 0x1919 device appears
```

### Phase 2: Query (on 0x1919 device)
```python
# Connect to 0x1919:0x1919, Usage Page 0xFF
send([0xAA, 0x10] + [0]*62)  # Query
send([0xAA, 0x17, 0,0,0,0x38,0,0, 2,0,2,6,0,2,3,0,4,0,9,1] + [0]*44)  # Config
send([0xAA, 0x11] + [0]*62)  # Start
send([0xAA, 0x1C] + [0]*62)  # Status
send([0xAA, 0x10] + [0]*62)  # Query again
send([0xAA, 0x12, 0,0,0,0x38] + [0]*58)  # Screen query
send([0xAA, 0x11] + [0]*62)  # Start
send([0xAA, 0x1C] + [0]*62)  # Status
```

### Phase 3: Download Mode (TRIGGERS "Downloading" SCREEN)
```python
send([0xAA, 0x1B, 0,0,0,0x38] + [0]*58)  # Prepare
send([0xAA, 0xE3, 0,0,0,1,0,0,1] + [0]*55)  # DOWNLOAD MODE! ✅
send([0xAA, 0x14, 0,0,0,0x38] + [0]*58)  # Screen prep
# Screen now shows "Downloading"
```

### Phase 4: Transfer Setup
```python
file_size = len(qgif_data)
fps = 8
frame_count = 3
setup = [0xAA, 0x15, 0,0, 0,0x38, 0,0, 0, frame_count, fps, 0,0,
         file_size & 0xFF, (file_size >> 8) & 0xFF, (file_size >> 16) & 0xFF]
send(setup + [0]*(64-len(setup)))
send([0xAA, 0x15, 0x38,0, 0,0x38] + [0]*58)  # Second setup
send([0xAA, 0x15, 0x70,0, 0,0x10] + [0]*58)  # Third setup
send([0xAA, 0x16, 0,0,0,0x38] + [0]*58)      # Ready
send([0xAA, 0x18, 0,0,0,1,0,0, fps] + [0]*55)  # Frame info
```

### Phase 5: Data Transfer
```python
CHUNK_SIZE = 56  # 64 - 8 header bytes
offset = 0
while offset < file_size:
    chunk = qgif_data[offset:offset + CHUNK_SIZE]
    # Pad to 56 bytes if needed
    if len(chunk) < CHUNK_SIZE:
        chunk = chunk + bytes(CHUNK_SIZE - len(chunk))
    # Data packet: AA 19 [offset_L] [offset_H] 00 38 00 00 [56 bytes data]
    packet = [0xAA, 0x19, offset & 0xFF, (offset >> 8) & 0xFF, 0, 0x38, 0, 0] + list(chunk)
    send(packet)
    offset += CHUNK_SIZE
```

### Phase 6: Finalize
```python
send([0xAA, 0x1C] + [0]*62)  # Status
send([0xAA, 0x1A, 0,0,0,0x38] + [0]*58)  # End
```

## QGIF Format

The RT82 uses a proprietary **QGIF** format for images. The official web tool uses WebAssembly to compress.

### QGIF Compression (from qgif.js/qgif.wasm)

The WASM function signature:
```javascript
compress_video_wasm(inputPattern, outputPath, flag, frameCount) -> returnCode
// Example: compress_video_wasm("/input_X.png", "/output.qgif", 0, 40)
```

- **inputPattern:** PNG frames named `input_0.png`, `input_1.png`, etc.
- **outputPath:** Output file `/output.qgif`
- **flag:** 0 (compression flag?)
- **frameCount:** Number of frames

### QGIF Header Structure (from captured data)

```
Bytes 0-3:  "QGIF" magic (0x51 0x47 0x49 0x46)
Bytes 4-5:  0x00 0x0F (possibly width/16 = 240/16 = 15)
Bytes 6-7:  0x00 [frame_count] (e.g., 0x00 0x28 = 40 frames)
Bytes 8-9:  0x3B 0x21 (unknown)
Bytes 10+:  0xFF padding or palette data
...
Compressed frame data follows
```

### Example QGIF Stats

From a test upload:
- **Input:** 40-frame GIF at 8 FPS, 240x135 pixels
- **Uncompressed RGB565:** 40 × 64,800 = 2,592,000 bytes
- **QGIF Output:** 389,369 bytes (~6.6x compression)

## What Works / What Doesn't

### ✅ Working
- Device detection and connection
- Two-step init (0x36B0 → 0x1919)
- Download mode trigger (screen shows "Downloading")
- Data packet transfer
- Finalization (screen exits download mode)

### ⚠️ Partial
- Sending raw RGB565 with fake QGIF header shows garbled pixels
- This confirms data reaches the display but format is wrong

### ❌ Not Working
- Raw RGB565 display (device requires QGIF compression)
- Native QGIF encoding (need to port WASM or reverse engineer format)

## Next Steps

1. **Option A:** Port qgif.wasm to Python/native (complex)
2. **Option B:** Use Node.js to call the WASM encoder
3. **Option C:** Capture QGIF data from web tool, upload via CLI
4. **Option D:** Fully reverse-engineer QGIF compression algorithm

## Captured Packet Examples

### Download Mode Trigger (from web capture)
```
#16 [0x1B]: aa 1b 00 00 00 38 00 00 ...  (Prepare)
#17 [0xE3]: aa e3 00 00 00 01 00 00 01 ...  (DOWNLOAD MODE!)
#18 [0x14]: aa 14 00 00 00 38 00 00 ...  (Screen prep)
```

### Data Transfer Start
```
#19 [0x15]: aa 15 00 00 00 38 00 00 00 01 07 00 00 f9 f0 05 ...  (Setup with size 0x05F0F9=389369)
#24 [0x19]: aa 19 00 00 00 38 00 00 51 47 49 46 ...  (First data - QGIF magic!)
#25 [0x19]: aa 19 38 00 00 38 00 00 ...  (offset 0x38=56)
#26 [0x19]: aa 19 70 00 00 38 00 00 ...  (offset 0x70=112)
```

## Tools Used

1. **Chrome DevTools Console** - JavaScript interceptor for WebHID
2. **ioreg** (macOS) - USB device enumeration
3. **hidapi** (Python) - HID communication
4. **curl** - Fetch website JavaScript for analysis

## References

- Official web tool: https://image.rdmctmzt.com/
- QGIF WASM source: https://image.rdmctmzt.com/qgif.js
- Similar project (GMK87): https://github.com/codedgar/gmk87-node
