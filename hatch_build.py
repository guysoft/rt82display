"""Custom hatch build hook that compiles the native QGIF encoder.

The wasm2c-generated C sources are compiled into a platform-specific binary
and bundled inside the wheel at rt82display/_bin/test_qgif[.exe].
"""

import os
import platform as platform_mod
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

C_SOURCES = [
    "qgif_generated.c",
    "wasm-rt-impl.c",
    "wasm-rt-mem-impl.c",
    "wasm_syscalls.c",
    "test_qgif.c",
]


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return

        src_dir = Path(self.root) / "wasm2c_runtime"
        if not src_dir.exists():
            return

        ext = ".exe" if sys.platform == "win32" else ""
        binary_name = f"test_qgif{ext}"

        bin_dir = Path(self.root) / "rt82display" / "_bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        output = bin_dir / binary_name

        sources = [str(src_dir / f) for f in C_SOURCES]

        if sys.platform == "win32":
            cmd = [
                "cl.exe", "/O2", "/DNDEBUG",
                "/DWASM_RT_MEMCHECK_BOUNDS_CHECK=1",
                f"/I{src_dir}",
                *sources,
                f"/Fe:{output}",
            ]
        else:
            cmd = [
                "cc", "-O2", "-DNDEBUG",
                "-DWASM_RT_MEMCHECK_BOUNDS_CHECK=1",
                f"-I{src_dir}",
            ]
            archflags = os.environ.get("ARCHFLAGS", "")
            if archflags:
                cmd += archflags.split()
            cmd += [*sources, "-lm", "-o", str(output)]

        subprocess.check_call(cmd)

        if sys.platform != "win32":
            output.chmod(0o755)

        build_data["force_include"][str(output)] = f"rt82display/_bin/{binary_name}"
        build_data["tag"] = f"py3-none-{self._platform_tag()}"

    def _platform_tag(self):
        if sys.platform == "linux":
            return os.environ.get(
                "AUDITWHEEL_PLAT", f"linux_{platform_mod.machine()}"
            )

        if sys.platform == "darwin":
            archflags = os.environ.get("ARCHFLAGS", "")
            host_plat = os.environ.get("_PYTHON_HOST_PLATFORM", "")
            if "x86_64" in archflags or "x86_64" in host_plat:
                return "macosx_10_9_x86_64"
            if "arm64" in archflags or "arm64" in host_plat:
                return "macosx_11_0_arm64"
            if platform_mod.machine() == "arm64":
                return "macosx_11_0_arm64"
            return "macosx_10_9_x86_64"

        if sys.platform == "win32":
            return "win_amd64"

        import sysconfig
        return sysconfig.get_platform().replace("-", "_").replace(".", "_")
