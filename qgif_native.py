"""
Native QGIF encoder using wasm2c-compiled library.

This module provides a Python interface to the native QGIF encoder,
which is the exact same code as the web tool's qgif.wasm, compiled to C.
"""

import ctypes
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image


# Determine shared library extension
if platform.system() == "Darwin":
    LIB_EXT = "dylib"
elif platform.system() == "Windows":
    LIB_EXT = "dll"
else:
    LIB_EXT = "so"

# Path to the shared library and test binary
LIB_DIR = Path(__file__).parent / "wasm2c_runtime"
LIB_PATH = LIB_DIR / f"libqgif.{LIB_EXT}"
TEST_BIN = LIB_DIR / "test_qgif"


class QGIFEncoder:
    """
    Native QGIF encoder wrapper.
    
    Uses the wasm2c-compiled QGIF encoder for byte-exact compatibility
    with the official web tool.
    """
    
    # Display dimensions (must be divisible by 4 for WASM encoder)
    WIDTH = 240
    HEIGHT = 136  # 135 rounded up to divisible by 4
    
    def __init__(self):
        if not TEST_BIN.exists():
            raise FileNotFoundError(
                f"Native encoder not found: {TEST_BIN}\n"
                f"Run: cd wasm2c_runtime && ./build.sh && "
                f"cc -O2 ... test_qgif.c -o test_qgif"
            )
    
    def encode_gif(
        self,
        gif_path: Path,
        output_path: Path,
        flag: int = 0
    ) -> bool:
        """
        Encode a GIF file to QGIF format.
        
        Args:
            gif_path: Path to input GIF file
            output_path: Path for output QGIF file
            flag: Compression flag (0 = default)
        
        Returns:
            True if encoding succeeded, False otherwise
        """
        gif_path = Path(gif_path)
        output_path = Path(output_path)
        
        if not gif_path.exists():
            raise FileNotFoundError(f"GIF not found: {gif_path}")
        
        # Load GIF and extract frames as PNG
        with Image.open(gif_path) as gif:
            frames = []
            try:
                while True:
                    # Convert to RGBA, resize to required dimensions
                    frame = gif.copy().convert('RGBA')
                    frame = frame.resize(
                        (self.WIDTH, self.HEIGHT),
                        Image.Resampling.LANCZOS
                    )
                    frames.append(frame)
                    gif.seek(gif.tell() + 1)
            except EOFError:
                pass
        
        if not frames:
            raise ValueError("No frames found in GIF")
        
        # Create temp directory for PNG frames
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Save frames as PNG files
            # The WASM expects pattern like "input_X.png" where X is replaced by frame number
            for i, frame in enumerate(frames):
                png_path = tmpdir / f"input_{i}.png"
                frame.save(png_path, 'PNG')
            
            # Call the native encoder via subprocess
            input_pattern = str(tmpdir / "input_X.png")
            output_str = str(output_path.absolute())
            
            result = subprocess.run(
                [str(TEST_BIN), input_pattern, output_str, str(len(frames))],
                capture_output=True,
                text=True
            )
            
            return result.returncode == 0
    
    def encode_frames(
        self,
        frames: list[Image.Image],
        output_path: Path,
        flag: int = 0
    ) -> bool:
        """
        Encode PIL Image frames to QGIF format.
        
        Args:
            frames: List of PIL Image frames
            output_path: Path for output QGIF file
            flag: Compression flag (0 = default)
        
        Returns:
            True if encoding succeeded, False otherwise
        """
        output_path = Path(output_path)
        
        if not frames:
            raise ValueError("No frames provided")
        
        # Create temp directory for PNG frames
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Resize and save frames as PNG
            for i, frame in enumerate(frames):
                resized = frame.convert('RGBA').resize(
                    (self.WIDTH, self.HEIGHT),
                    Image.Resampling.LANCZOS
                )
                png_path = tmpdir / f"input_{i}.png"
                resized.save(png_path, 'PNG')
            
            # Call the native encoder via subprocess
            input_pattern = str(tmpdir / "input_X.png")
            output_str = str(output_path.absolute())
            
            result = subprocess.run(
                [str(TEST_BIN), input_pattern, output_str, str(len(frames))],
                capture_output=True,
                text=True
            )
            
            return result.returncode == 0


def encode_qgif(gif_path: Path, output_path: Path, flag: int = 0) -> bool:
    """
    Convenience function to encode a GIF to QGIF.
    
    Args:
        gif_path: Path to input GIF
        output_path: Path for output QGIF
        flag: Compression flag (0 = default)
    
    Returns:
        True if successful
    """
    encoder = QGIFEncoder()
    return encoder.encode_gif(gif_path, output_path, flag)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python qgif_native.py <input.gif> <output.qgif> [flag]")
        sys.exit(1)
    
    gif_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    flag = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    
    print(f"Encoding {gif_path} -> {output_path} (flag={flag})")
    
    try:
        if encode_qgif(gif_path, output_path, flag):
            print(f"✅ Success! Output: {output_path} ({output_path.stat().st_size:,} bytes)")
        else:
            print("❌ Encoding failed")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
