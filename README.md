# RT82 Display CLI

Upload GIFs to your Epomaker RT82 keyboard's LCD screen from the command line.

## Installation

### Install from PyPI

```bash
pip install rt82display
```

This installs the CLI tool and all Python dependencies. You can immediately
use `rt82display list`, `rt82display info`, and upload pre-encoded `.qgif` files.

### Linux: udev rules (required for non-root access)

```bash
# Download the rules file from the repo
sudo curl -o /etc/udev/rules.d/99-rt82.rules \
  https://raw.githubusercontent.com/guysoft/rt82display/main/udev/99-rt82.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug and replug the keyboard.

**macOS:** No extra setup required.

### Optional: Build the native QGIF encoder

The native encoder is needed to upload `.gif` files directly (auto-converts
GIF to QGIF). Without it you can still upload pre-encoded `.qgif` files.

```bash
git clone https://github.com/guysoft/rt82display.git
cd rt82display/wasm2c_runtime
./build.sh
```

**Requirements:** A C compiler (gcc/clang). The WABT runtime headers are
included in the repository.

### Development install

To install from a local checkout instead of PyPI:

```bash
git clone https://github.com/guysoft/rt82display.git
cd rt82display
pip install -e .
```

## Usage

### Upload a GIF (auto-encodes to QGIF)

```bash
rt82display upload my_animation.gif
```

### Upload a pre-encoded QGIF file

```bash
rt82display upload my_animation.qgif
```

### Encode a GIF without uploading

```bash
rt82display encode input.gif output.qgif
```

### List connected devices

```bash
rt82display list
```

### Show device/protocol info

```bash
rt82display info
```

## Example

A test GIF is included in the repository to verify your setup works:

```bash
rt82display upload capture_test.gif
```

![Example GIF](capture_test.gif)

## Limitations

- **64KB file size limit**: The RT82 firmware has a ~64KB buffer. Complex GIFs with many color transitions may exceed this limit and cause display artifacts.
- **Best results**: Use simple GIFs with solid colors and minimal patterns.
- If your GIF exceeds the limit, you'll see a warning but can still attempt the upload.

## Alternative: Web Tool Encoding

If the native encoder doesn't work for your GIF, you can capture QGIF from the official web tool:

1. Open https://image.rdmctmzt.com/ in Chrome
2. Paste this in DevTools Console (F12):
```javascript
window._p=[];const _s=HIDDevice.prototype.sendReport;HIDDevice.prototype.sendReport=function(i,d){window._p.push(Array.from(new Uint8Array(d)));return _s.call(this,i,d)};window.dl=()=>{const c=[];window._p.filter(x=>x[1]===0x19).forEach(x=>c.push(...x.slice(8)));if(!c.length){console.log('No data!');return}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([new Uint8Array(c)]));a.download='animation.qgif';a.click();console.log('Saved',c.length,'bytes')};console.log('Ready! Upload GIF, Download to Device, then run: dl()');
```
3. Upload your GIF and click "Download to Device"
4. Run `dl()` in console to save the QGIF file
5. Upload via CLI: `rt82display upload animation.qgif`

## Technical Details

- **Display**: 240×135 RGB565
- **Format**: Proprietary QGIF (RLE compressed)
- **Protocol**: USB HID with two-stage device activation
- **Encoder**: wasm2c-compiled from official qgif.wasm

See [PROTOCOL.md](PROTOCOL.md) and [QGIF.md](QGIF.md) for technical documentation.

## Troubleshooting

See [AGENTS.md](AGENTS.md) for detailed troubleshooting steps.

### Quick fixes:

- **Device not found**: Unplug and replug keyboard
- **Permission denied (Linux)**: Install the udev rules (see Prerequisites above) and replug
- **Screen stuck on "Downloading"**: Unplug and replug
- **Garbled display**: GIF likely exceeds 64KB limit, try simpler patterns
