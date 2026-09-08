"""Hardware profiling and unified memory advisor for Deeper Notebook.

Detects platform architecture, Apple Silicon chip family, and available unified
memory to provide optimal llama.cpp engine settings (context window size, GGUF
quantization tier, Metal FlashAttention, and KV cache quantization).
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Any, Optional


def get_hardware_profile() -> dict[str, Any]:
    """Profile host hardware and generate unified-memory-aware recommendations."""
    system = platform.system()
    machine = platform.machine()
    is_mac = system == "Darwin"
    is_apple_silicon = is_mac and machine == "arm64"

    chip_name = platform.processor() or machine
    total_ram_bytes: Optional[int] = None

    if is_mac:
        # Query total RAM in bytes
        try:
            res = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip().isdigit():
                total_ram_bytes = int(res.stdout.strip())
        except Exception:
            pass

        # Query chip brand string (e.g. "Apple M1 Max", "Apple M3 Pro")
        try:
            res = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                chip_name = res.stdout.strip()
        except Exception:
            pass
    elif system == "Linux":
        try:
            total_ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except Exception:
            pass
    elif system == "Windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MemoryStatus()
            stat.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_ram_bytes = int(stat.ullTotalPhys)
        except Exception:
            pass

    total_ram_gb = round(total_ram_bytes / (1024**3), 1) if total_ram_bytes else 0.0

    # Compute recommendations
    if is_apple_silicon:
        recommended_flash_attn = True
        recommended_kv_quant = "q8_0"
        if total_ram_gb >= 64:
            recommended_context = 65536
            recommended_quant = "Q8_0 or Q6_K"
            tier_name = f"Ultra / Heavy Duty ({total_ram_gb}GB Unified Memory)"
            guidance = "Supports 70B Q4 or 32B Q8 local models with 64k+ context windows."
        elif total_ram_gb >= 32:
            recommended_context = 32768
            recommended_quant = "Q5_K_M or Q8_0"
            tier_name = f"Pro / High Performance ({total_ram_gb}GB Unified Memory)"
            guidance = "Ideal for 14B and 32B local models with 32k context windows."
        elif total_ram_gb >= 16:
            recommended_context = 16384
            recommended_quant = "Q4_K_M"
            tier_name = f"Balanced ({total_ram_gb}GB Unified Memory)"
            guidance = "Great for 7B/8B/14B Q4 models with 16k context."
        else:
            recommended_context = 8192
            recommended_quant = "Q4_K_S"
            tier_name = f"Standard ({total_ram_gb}GB Unified Memory)"
            guidance = "Optimized for 7B/8B Q4 models with 8k context."
    else:
        recommended_flash_attn = False
        recommended_kv_quant = "f16"
        recommended_context = 8192 if total_ram_gb < 16 else 16384
        recommended_quant = "Q4_K_M"
        tier_name = f"Standard Workstation ({total_ram_gb}GB RAM)"
        guidance = "Standard CPU/GPU architecture with separate VRAM."

    return {
        "system": system,
        "machine": machine,
        "chip_name": chip_name,
        "is_apple_silicon": is_apple_silicon,
        "total_ram_bytes": total_ram_bytes or 0,
        "total_ram_gb": total_ram_gb,
        "tier_name": tier_name,
        "guidance": guidance,
        "recommended_context": recommended_context,
        "recommended_quant": recommended_quant,
        "recommended_flash_attn": recommended_flash_attn,
        "recommended_kv_quant": recommended_kv_quant,
    }
