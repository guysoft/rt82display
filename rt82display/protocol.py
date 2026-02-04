"""Protocol definitions for RT82 display communication.

This module defines the packet structure and commands for communicating
with the RT82 keyboard's LCD screen over HID.

Protocol captured from WebHID traffic at https://image.rdmctmzt.com/
"""

from dataclasses import dataclass
from typing import Iterator
import struct

from .image import ProcessedGif, GifFrame, DISPLAY_WIDTH, DISPLAY_HEIGHT

# ============================================================================
# Protocol Constants (CAPTURED FROM WEBHID TRAFFIC)
# ============================================================================

PACKET_SIZE = 64      # HID report size
REPORT_ID = 0x00      # HID report ID

# Magic byte - all commands start with this
MAGIC_BYTE = 0xAA  # 170 decimal

# Command bytes (captured from traffic)
CMD_INIT = 0xE2           # Init device
CMD_HANDSHAKE = 0xE0      # Handshake
CMD_QUERY = 0x10          # Query device
CMD_STATUS = 0x1C         # Status/heartbeat
CMD_CONFIG = 0x17         # Configuration
CMD_START = 0x11          # Start 
CMD_SCREEN_QUERY = 0x12   # Screen query
CMD_PREPARE = 0x1B        # Prepare for download
CMD_DOWNLOAD_MODE = 0xE3  # Enter download mode (triggers "Downloading" on screen!)
CMD_SCREEN_PREP = 0x14    # Prepare screen
CMD_TRANSFER_SETUP = 0x15 # Transfer setup (includes file size)
CMD_TRANSFER_READY = 0x16 # Transfer ready
CMD_FRAME_INFO = 0x18     # Frame/FPS info
CMD_DATA = 0x19           # Data packet

# Data packet parameters
DATA_CHUNK_SIZE = 56      # Bytes of actual data per packet (64 - 8 header bytes)
SCREEN_PARAM = 0x38       # Screen parameter (appears in most commands)

# QGIF format magic bytes
QGIF_MAGIC = b'QGIF'


@dataclass
class Packet:
    """A single HID packet to be sent to the device."""
    
    report_id: int
    data: bytes
    
    def __len__(self) -> int:
        return len(self.data)


def _make_packet(cmd: int, data: bytes = b'') -> Packet:
    """Create a 64-byte packet with magic byte and command."""
    packet_data = bytearray(PACKET_SIZE)
    packet_data[0] = MAGIC_BYTE
    packet_data[1] = cmd
    if data:
        packet_data[2:2+len(data)] = data[:PACKET_SIZE-2]
    return Packet(report_id=REPORT_ID, data=bytes(packet_data))


# ============================================================================
# Command Builders (based on captured traffic)
# ============================================================================

def build_init_packet() -> Packet:
    """Build init packet (0xE2)."""
    return _make_packet(CMD_INIT)


def build_handshake_packet() -> Packet:
    """Build handshake packet (0xE0)."""
    return _make_packet(CMD_HANDSHAKE)


def build_query_packet() -> Packet:
    """Build query packet (0x10)."""
    return _make_packet(CMD_QUERY)


def build_status_packet() -> Packet:
    """Build status packet (0x1C)."""
    return _make_packet(CMD_STATUS)


def build_config_packet() -> Packet:
    """Build config packet (0x17) with captured data."""
    # From capture: aa 17 00 00 00 38 00 00 02 00 02 06 00 02 03 00 04 00 09 01
    data = bytes([0x00, 0x00, 0x00, SCREEN_PARAM, 0x00, 0x00, 
                  0x02, 0x00, 0x02, 0x06, 0x00, 0x02, 0x03, 0x00, 0x04, 0x00, 0x09, 0x01])
    return _make_packet(CMD_CONFIG, data)


def build_start_packet() -> Packet:
    """Build start packet (0x11)."""
    return _make_packet(CMD_START)


def build_screen_query_packet() -> Packet:
    """Build screen query packet (0x12)."""
    # From capture: aa 12 00 00 00 38
    data = bytes([0x00, 0x00, 0x00, SCREEN_PARAM])
    return _make_packet(CMD_SCREEN_QUERY, data)


def build_prepare_packet() -> Packet:
    """Build prepare for download packet (0x1B)."""
    # From capture: aa 1b 00 00 00 38
    data = bytes([0x00, 0x00, 0x00, SCREEN_PARAM])
    return _make_packet(CMD_PREPARE, data)


def build_download_mode_packet(screen_count: int = 1) -> Packet:
    """Build download mode trigger packet (0xE3).
    
    This is the command that makes the screen show "Downloading"!
    
    From capture: aa e3 00 00 00 01 00 00 01
    """
    data = bytes([0x00, 0x00, 0x00, screen_count, 0x00, 0x00, screen_count])
    return _make_packet(CMD_DOWNLOAD_MODE, data)


def build_screen_prep_packet() -> Packet:
    """Build screen preparation packet (0x14)."""
    # From capture: aa 14 00 00 00 38
    data = bytes([0x00, 0x00, 0x00, SCREEN_PARAM])
    return _make_packet(CMD_SCREEN_PREP, data)


def build_transfer_setup_packet(offset: int, file_size: int, screen_index: int = 0, 
                                 frame_count: int = 1, fps: int = 8) -> Packet:
    """Build transfer setup packet (0x15).
    
    From capture: aa 15 00 00 00 38 00 00 00 01 07 00 00 f9 f0 05
    Structure: [offset_L] [offset_H] 00 38 00 00 [screen] [frames] [fps] 00 00 [size_L] [size_M] [size_H]
    """
    data = bytes([
        offset & 0xFF,           # Offset low
        (offset >> 8) & 0xFF,    # Offset high
        0x00,
        SCREEN_PARAM,
        0x00, 0x00,
        screen_index,            # Screen index
        frame_count,             # Frame count (or 1 for single)
        fps,                     # FPS (7-8 typical)
        0x00, 0x00,
        file_size & 0xFF,        # Size low byte
        (file_size >> 8) & 0xFF, # Size middle byte
        (file_size >> 16) & 0xFF # Size high byte
    ])
    return _make_packet(CMD_TRANSFER_SETUP, data)


def build_transfer_ready_packet() -> Packet:
    """Build transfer ready packet (0x16)."""
    # From capture: aa 16 00 00 00 38
    data = bytes([0x00, 0x00, 0x00, SCREEN_PARAM])
    return _make_packet(CMD_TRANSFER_READY, data)


def build_frame_info_packet(screen_count: int = 1, fps: int = 7) -> Packet:
    """Build frame info packet (0x18).
    
    From capture: aa 18 00 00 00 01 00 00 07
    """
    data = bytes([0x00, 0x00, 0x00, screen_count, 0x00, 0x00, fps])
    return _make_packet(CMD_FRAME_INFO, data)


def build_data_packet(offset: int, chunk: bytes) -> Packet:
    """Build data packet (0x19).
    
    Structure: aa 19 [offset_L] [offset_H] 00 38 00 00 [56 bytes data]
    """
    # Pad chunk to 56 bytes
    if len(chunk) < DATA_CHUNK_SIZE:
        chunk = chunk + bytes(DATA_CHUNK_SIZE - len(chunk))
    
    data = bytes([
        offset & 0xFF,           # Offset low
        (offset >> 8) & 0xFF,    # Offset high
        0x00,
        SCREEN_PARAM,
        0x00, 0x00
    ]) + chunk[:DATA_CHUNK_SIZE]
    
    return _make_packet(CMD_DATA, data)


# ============================================================================
# High-Level Transfer Functions
# ============================================================================

def build_qgif_transfer(qgif_data: bytes, frame_count: int = 1, fps: int = 8) -> Iterator[Packet]:
    """Build complete packet sequence for QGIF transfer.
    
    This replicates the exact sequence captured from the web interface.
    """
    file_size = len(qgif_data)
    
    # Phase 1: Init sequence
    yield build_init_packet()
    for _ in range(5):
        yield build_handshake_packet()
    
    # Phase 2: Device query
    yield build_query_packet()
    yield build_config_packet()
    yield build_start_packet()
    yield build_status_packet()
    yield build_query_packet()
    yield build_screen_query_packet()
    yield build_start_packet()
    yield build_status_packet()
    
    # Phase 3: Download mode trigger
    yield build_prepare_packet()
    yield build_download_mode_packet(screen_count=1)  # This shows "Downloading"!
    yield build_screen_prep_packet()
    
    # Phase 4: Transfer setup
    yield build_transfer_setup_packet(0, file_size, screen_index=0, frame_count=frame_count, fps=fps)
    yield build_transfer_setup_packet(SCREEN_PARAM, 0)  # Second setup packet
    yield build_transfer_setup_packet(0x70, 0)  # Third setup packet (0x70 = 112)
    yield build_transfer_ready_packet()
    yield build_frame_info_packet(screen_count=1, fps=fps)
    
    # Phase 5: Data transfer
    offset = 0
    while offset < file_size:
        chunk = qgif_data[offset:offset + DATA_CHUNK_SIZE]
        yield build_data_packet(offset, chunk)
        offset += DATA_CHUNK_SIZE


def build_raw_rgb565_transfer(gif: ProcessedGif, screen_index: int = 0) -> Iterator[Packet]:
    """Build packets for raw RGB565 transfer (may not work - device expects QGIF)."""
    all_data = b''.join(frame.data for frame in gif.frames)
    
    # Use same sequence as QGIF
    yield from build_qgif_transfer(all_data, frame_count=gif.total_frames, fps=8)


def count_packets_for_size(data_size: int) -> int:
    """Calculate number of packets needed for a given data size."""
    # Header packets (init, handshakes, queries, download mode, etc.)
    header_packets = 20
    
    # Data packets (56 bytes each)
    data_packets = (data_size + DATA_CHUNK_SIZE - 1) // DATA_CHUNK_SIZE
    
    return header_packets + data_packets


def is_qgif_data(data: bytes) -> bool:
    """Check if data is in QGIF format."""
    return len(data) >= 4 and data[:4] == QGIF_MAGIC
