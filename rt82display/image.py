"""Image processing for RT82 display.

Handles GIF loading, frame extraction, resizing, and RGB565 conversion.
"""

from pathlib import Path
from dataclasses import dataclass

from PIL import Image

# RT82 display dimensions (captured from WebHID: screenSize {width: 240, height: 135})
DISPLAY_WIDTH = 240
DISPLAY_HEIGHT = 135


@dataclass
class GifFrame:
    """A single frame from a GIF animation."""
    
    index: int
    data: bytes  # RGB565 data
    duration_ms: int  # Frame duration in milliseconds
    width: int
    height: int


@dataclass
class ProcessedGif:
    """A processed GIF ready for upload to the RT82."""
    
    frames: list[GifFrame]
    width: int
    height: int
    total_frames: int
    
    @property
    def is_animated(self) -> bool:
        """Check if this is an animated GIF."""
        return self.total_frames > 1
    
    @property
    def total_size(self) -> int:
        """Total size of all frame data in bytes."""
        return sum(len(f.data) for f in self.frames)


def rgb_to_rgb565(r: int, g: int, b: int) -> int:
    """Convert 24-bit RGB to 16-bit RGB565.
    
    RGB565 format:
    - 5 bits for Red (bits 15-11)
    - 6 bits for Green (bits 10-5)
    - 5 bits for Blue (bits 4-0)
    
    Args:
        r: Red component (0-255)
        g: Green component (0-255)
        b: Blue component (0-255)
    
    Returns:
        16-bit RGB565 value
    """
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def rgb565_to_bytes(rgb565: int) -> bytes:
    """Convert RGB565 value to 2 bytes (little-endian)."""
    return bytes([rgb565 & 0xFF, (rgb565 >> 8) & 0xFF])


def image_to_rgb565(img: Image.Image) -> bytes:
    """Convert a PIL Image to RGB565 byte array.
    
    Args:
        img: PIL Image in RGB mode
    
    Returns:
        Bytes containing RGB565 pixel data (2 bytes per pixel, little-endian)
    """
    # Ensure image is in RGB mode
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    pixels = img.load()
    width, height = img.size
    data = bytearray()
    
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            rgb565 = rgb_to_rgb565(r, g, b)
            data.extend(rgb565_to_bytes(rgb565))
    
    return bytes(data)


def resize_image(img: Image.Image, width: int = DISPLAY_WIDTH, height: int = DISPLAY_HEIGHT) -> Image.Image:
    """Resize image to fit the display, maintaining aspect ratio.
    
    The image is resized to fit within the display dimensions and centered
    on a black background if necessary.
    
    Args:
        img: PIL Image to resize
        width: Target width (default: DISPLAY_WIDTH)
        height: Target height (default: DISPLAY_HEIGHT)
    
    Returns:
        Resized PIL Image
    """
    # Calculate aspect ratios
    img_ratio = img.width / img.height
    target_ratio = width / height
    
    if img_ratio > target_ratio:
        # Image is wider than target - fit to width
        new_width = width
        new_height = int(width / img_ratio)
    else:
        # Image is taller than target - fit to height
        new_height = height
        new_width = int(height * img_ratio)
    
    # Resize with high quality
    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # Create black background and paste centered
    result = Image.new('RGB', (width, height), (0, 0, 0))
    x_offset = (width - new_width) // 2
    y_offset = (height - new_height) // 2
    
    # Handle transparency if present
    if resized.mode == 'RGBA':
        result.paste(resized, (x_offset, y_offset), resized)
    else:
        result.paste(resized, (x_offset, y_offset))
    
    return result


def load_gif(path: Path | str) -> ProcessedGif:
    """Load and process a GIF file for the RT82 display.
    
    Args:
        path: Path to the GIF file
    
    Returns:
        ProcessedGif containing all frames converted to RGB565
    
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file is not a valid image
    """
    path = Path(path)
    
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    
    try:
        img = Image.open(path)
    except Exception as e:
        raise ValueError(f"Failed to open image: {e}") from e
    
    frames: list[GifFrame] = []
    
    # Check if it's an animated GIF
    is_animated = hasattr(img, 'n_frames') and img.n_frames > 1
    n_frames = img.n_frames if is_animated else 1
    
    for frame_idx in range(n_frames):
        if is_animated:
            img.seek(frame_idx)
        
        # Get frame duration (default 100ms if not specified)
        duration = img.info.get('duration', 100)
        if duration == 0:
            duration = 100
        
        # Convert to RGB and resize
        frame_img = img.convert('RGB')
        resized = resize_image(frame_img)
        
        # Convert to RGB565
        rgb565_data = image_to_rgb565(resized)
        
        frames.append(GifFrame(
            index=frame_idx,
            data=rgb565_data,
            duration_ms=duration,
            width=DISPLAY_WIDTH,
            height=DISPLAY_HEIGHT,
        ))
    
    return ProcessedGif(
        frames=frames,
        width=DISPLAY_WIDTH,
        height=DISPLAY_HEIGHT,
        total_frames=len(frames),
    )


def load_image(path: Path | str) -> ProcessedGif:
    """Load any image file (GIF, PNG, JPG, etc.) for the RT82 display.
    
    This is an alias for load_gif that works with any image format.
    Non-animated images will be returned as a single-frame ProcessedGif.
    
    Args:
        path: Path to the image file
    
    Returns:
        ProcessedGif containing the image converted to RGB565
    """
    return load_gif(path)
