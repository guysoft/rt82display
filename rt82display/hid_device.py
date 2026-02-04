"""HID device communication for RT82 display.

Handles finding, connecting to, and communicating with the RT82 keyboard
over USB HID.

NOTE: The VID/PID values are placeholders that need to be updated based on
captured WebHID traffic from the official tool.
"""

from dataclasses import dataclass
from typing import Optional
import time

import hid

from .protocol import Packet

# ============================================================================
# Device Constants (CAPTURED FROM WEBHID TRAFFIC)
# ============================================================================

# LCD interface - THE CORRECT ONE FOR DISPLAY COMMUNICATION
# VID:PID 0x1919:0x1919 with Usage Page 0x0001, Usage 0x0002, Interface 0
RT82_LCD_VENDOR_ID = 0x1919   # 6425 decimal
RT82_LCD_PRODUCT_ID = 0x1919  # 6425 decimal
RT82_LCD_USAGE_PAGE = 0x0001  # Generic Desktop usage page
RT82_LCD_USAGE = 0x0002       # Mouse usage (but it's actually the LCD!)

# Keyboard interface (not for LCD)
RT82_KBD_VENDOR_ID = 0x36B0   # 14000 decimal
RT82_KBD_PRODUCT_ID = 0x30A3  # 12451 decimal

# For backwards compatibility
RT82_VENDOR_ID = RT82_LCD_VENDOR_ID
RT82_PRODUCT_ID = RT82_LCD_PRODUCT_ID
RT82_VENDOR_ID_ALT = RT82_KBD_VENDOR_ID
RT82_PRODUCT_ID_ALT = RT82_KBD_PRODUCT_ID

# Known VID/PID pairs for RT82
KNOWN_VIDS = [0x1919, 0x36B0]
KNOWN_PRODUCT_NAMES = ["RT82", "Epomaker"]

# Communication settings
WRITE_TIMEOUT_MS = 1000
INTER_PACKET_DELAY_MS = 0  # No delay needed - device handles packets quickly


@dataclass
class DeviceInfo:
    """Information about a connected HID device."""
    
    vendor_id: int
    product_id: int
    product_name: str
    manufacturer: str
    serial_number: str
    path: bytes
    usage_page: int = 0
    usage: int = 0
    interface_number: int = -1
    
    @property
    def vid_pid(self) -> str:
        """Format VID:PID as hex string."""
        return f"{self.vendor_id:04X}:{self.product_id:04X}"
    
    def __str__(self) -> str:
        return f"{self.product_name} ({self.vid_pid})"


class RT82Device:
    """HID interface to the RT82 keyboard display."""
    
    def __init__(self, device_info: DeviceInfo):
        """Initialize with device info.
        
        Args:
            device_info: Information about the device to connect to
        """
        self.info = device_info
        self._device: Optional[hid.device] = None
    
    @property
    def is_connected(self) -> bool:
        """Check if device is currently connected."""
        return self._device is not None
    
    def connect(self) -> None:
        """Open connection to the device.
        
        Raises:
            ConnectionError: If device cannot be opened
        """
        if self._device is not None:
            return
        
        try:
            self._device = hid.device()
            self._device.open_path(self.info.path)
            self._device.set_nonblocking(False)
        except Exception as e:
            self._device = None
            raise ConnectionError(f"Failed to open device: {e}") from e
    
    def disconnect(self) -> None:
        """Close the device connection."""
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
    
    def send_packet(self, packet: Packet) -> None:
        """Send a single packet to the device.
        
        Args:
            packet: The packet to send
        
        Raises:
            ConnectionError: If not connected
            IOError: If write fails
        """
        if self._device is None:
            raise ConnectionError("Device not connected")
        
        # Send packet data directly (no report ID prefix for this device)
        try:
            written = self._device.write(packet.data)
            if written < 0:
                raise IOError(f"Write failed with code {written}")
        except Exception as e:
            raise IOError(f"Failed to send packet: {e}") from e
    
    def send_feature_report(self, report_id: int, data: bytes) -> None:
        """Send a feature report to the device.
        
        Args:
            report_id: The report ID
            data: Report data
        
        Raises:
            ConnectionError: If not connected
            IOError: If write fails
        """
        if self._device is None:
            raise ConnectionError("Device not connected")
        
        # Prepare data with report ID
        report = bytes([report_id]) + data
        
        try:
            self._device.send_feature_report(report)
        except Exception as e:
            raise IOError(f"Failed to send feature report: {e}") from e
    
    def __enter__(self) -> "RT82Device":
        """Context manager entry - connect to device."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - disconnect from device."""
        self.disconnect()


# Usage page and usage for LCD HID interface
LCD_USAGE_PAGE = 0x0001  # Generic Desktop
LCD_USAGE = 0x0002       # Mouse (but actually LCD)


def find_devices() -> list[DeviceInfo]:
    """Find all potential RT82 display devices.
    
    Returns:
        List of DeviceInfo for found devices
    """
    devices = []
    seen_paths = set()  # Avoid duplicates
    
    for device_dict in hid.enumerate():
        vid = device_dict.get('vendor_id', 0)
        pid = device_dict.get('product_id', 0)
        product = device_dict.get('product_string', '') or ''
        manufacturer = device_dict.get('manufacturer_string', '') or ''
        path = device_dict.get('path', b'')
        usage_page = device_dict.get('usage_page', 0)
        usage = device_dict.get('usage', 0)
        interface = device_dict.get('interface_number', -1)
        
        # Skip if we've already seen this path
        if path in seen_paths:
            continue
        
        # Check for primary LCD interface (0x36B0:0x30A3)
        is_primary = (vid == RT82_VENDOR_ID and pid == RT82_PRODUCT_ID)
        
        # Check for secondary interface (0x1919:0x1919)
        is_secondary = (vid == RT82_VENDOR_ID_ALT and pid == RT82_PRODUCT_ID_ALT)
        
        # Also check by product name
        is_known_product = any(name.lower() in product.lower() for name in KNOWN_PRODUCT_NAMES)
        is_known_vid = vid in KNOWN_VIDS
        
        if is_primary or is_secondary or (is_known_vid and is_known_product):
            seen_paths.add(path)
            devices.append(DeviceInfo(
                vendor_id=vid,
                product_id=pid,
                product_name=product or f"Device {vid:04X}:{pid:04X}",
                manufacturer=manufacturer or "Unknown",
                serial_number=device_dict.get('serial_number', '') or '',
                path=path,
                usage_page=usage_page,
                usage=usage,
                interface_number=interface,
            ))
    
    # Sort to prioritize:
    # 1. The correct LCD interface: VID 0x1919, Usage Page 0x0001, Usage 0x0002
    # 2. Other interfaces as fallback
    def sort_key(d: DeviceInfo) -> tuple:
        is_lcd_interface = (
            d.usage_page == LCD_USAGE_PAGE and 
            d.usage == LCD_USAGE and
            d.vendor_id == RT82_LCD_VENDOR_ID and
            d.product_id == RT82_LCD_PRODUCT_ID
        )
        return (not is_lcd_interface, d.interface_number)
    
    devices.sort(key=sort_key)
    
    return devices


def find_rt82() -> Optional[DeviceInfo]:
    """Find the first RT82 display device.
    
    Returns:
        DeviceInfo if found, None otherwise
    """
    devices = find_devices()
    return devices[0] if devices else None


def open_device(device_info: Optional[DeviceInfo] = None) -> RT82Device:
    """Open a connection to an RT82 device.
    
    Args:
        device_info: Specific device to open, or None to find automatically
    
    Returns:
        Connected RT82Device
    
    Raises:
        ConnectionError: If no device found or cannot connect
    """
    if device_info is None:
        device_info = find_rt82()
        if device_info is None:
            raise ConnectionError(
                "No RT82 device found. Make sure the keyboard is connected "
                "and the display module is attached."
            )
    
    device = RT82Device(device_info)
    device.connect()
    return device
