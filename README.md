# RT82 Display CLI

Upload GIFs to your Epomaker RT82 keyboard's LCD screen from the command line.

## Installation

```bash
cd rt82display
uv pip install -e .
```

## Usage

### Upload a pre-encoded QGIF file

```bash
rt82display upload my_animation.qgif
```

### Current Workflow (Encoding via Web Tool)

Until native encoding is fully working, use the web tool to encode GIFs:

1. **Open** https://image.rdmctmzt.com/ in Chrome

2. **Paste this capture script** in DevTools Console (F12):
```javascript
window._p=[];const _s=HIDDevice.prototype.sendReport;HIDDevice.prototype.sendReport=function(i,d){window._p.push(Array.from(new Uint8Array(d)));return _s.call(this,i,d)};window.dl=()=>{const c=[];window._p.filter(x=>x[1]===0x19).forEach(x=>c.push(...x.slice(8)));if(!c.length){console.log('No data!');return}const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([new Uint8Array(c)]));a.download='animation.qgif';a.click();console.log('Saved',c.length,'bytes')};console.log('Ready! Upload GIF, Download to Device, then run: dl()');
```

3. **Upload your GIF** to the web tool

4. **Click "Download to Device"** (wait for completion)

5. **Run `dl()`** in console to save the QGIF file

6. **Upload via CLI**:
```bash
rt82display upload ~/Downloads/animation.qgif
```

## Commands

- `rt82display list` - List connected devices
- `rt82display info` - Show device info  
- `rt82display upload <file.qgif>` - Upload QGIF to display

## Technical Details

- Display: 240×135 RGB565
- Format: Proprietary QGIF (compressed)
- Protocol: USB HID with two-stage device activation

See [PROTOCOL.md](PROTOCOL.md) and [QGIF.md](QGIF.md) for technical documentation.

## Status

- ✅ Upload protocol fully working
- ⚠️ Native QGIF encoding in progress (use web tool for now)
- ✅ Device detection and connection
