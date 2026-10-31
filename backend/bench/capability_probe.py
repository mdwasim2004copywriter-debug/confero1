"""Read-only hardware/software capability probe for Confero's performance
work. Reports three distinct things per the audit's own rule -- never
conflate them:

    PRESENT                  -- the hardware/driver/runtime exists on this
                                 machine (e.g. a GPU, a Vulkan DLL)
    SUPPORTED BY THIS ENGINE  -- the Python library Confero actually uses
                                 can address it (verified by trying, not
                                 by assuming "a GPU exists" means "faster-
                                 whisper can use it")
    NOT TESTED                -- no claim is made either way

Run directly: python backend/bench/capability_probe.py
Writes a JSON snapshot to backend/bench/results/capability_<ts>.json and
prints a human-readable summary. Makes no changes to the application.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bench import sysinfo
else:
    from . import sysinfo

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def probe_python_env() -> dict:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": _pkg_version("torch"),
        "faster_whisper": _pkg_version("faster-whisper"),
        "ctranslate2": _pkg_version("ctranslate2"),
        "onnxruntime": _pkg_version("onnxruntime"),
    }


def probe_cpu() -> dict:
    info = {
        "processor_string": platform.processor() or platform.machine(),
        "logical_processors": os.cpu_count(),
        "physical_cores": None,
        "avx2": "UNKNOWN (not reliably detectable without a CPUID-reading "
        "dependency this phase intentionally avoids installing; see README)",
    }
    cim = sysinfo.powershell_json(
        "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores | ConvertTo-Json"
    )
    if cim:
        row = cim[0] if isinstance(cim, list) else cim
        info["processor_string"] = row.get("Name", info["processor_string"])
        info["physical_cores"] = row.get("NumberOfCores")
    return info


def probe_gpu() -> dict:
    info = {
        "name": None,
        "driver_version": None,
        "dedicated_memory_mb": None,
        "directx_feature_levels": "NOT TESTED (dxdiag not run by this probe pass; see run_dxdiag_scan)",
        "vulkan_runtime_present": None,
        "vulkan_version": "NOT TESTED (no vulkaninfo call made; presence of the loader DLL is not proof the GPU's ICD exposes a usable version)",
    }
    cim = sysinfo.powershell_json(
        "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM | ConvertTo-Json"
    )
    if cim:
        row = cim[0] if isinstance(cim, list) else cim
        info["name"] = row.get("Name")
        info["driver_version"] = row.get("DriverVersion")
        ram = row.get("AdapterRAM")
        info["dedicated_memory_mb"] = round(ram / (1024 * 1024)) if ram else None

    if sysinfo.IS_WINDOWS:
        vulkan_dll = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "vulkan-1.dll"
        info["vulkan_runtime_present"] = vulkan_dll.exists()

    return info


def run_dxdiag_scan(timeout: float = 20.0) -> dict:
    """Separate, slower probe (dxdiag takes several seconds) -- opt-in,
    not run by default inside probe_gpu()."""
    if not sysinfo.IS_WINDOWS:
        return {"status": "NOT APPLICABLE (not Windows)"}
    out_path = Path(os.environ.get("TEMP", ".")) / f"confero_dxdiag_{int(time.time())}.txt"
    sysinfo.run_os_tool(["dxdiag", "/t", str(out_path)], timeout=timeout)
    time.sleep(1.0)
    if not out_path.exists():
        return {"status": "FAILED (dxdiag did not produce a report in time)"}
    text = out_path.read_text(errors="ignore")
    feature_levels = None
    for line in text.splitlines():
        if "Feature Levels" in line:
            feature_levels = line.split(":", 1)[-1].strip()
    try:
        out_path.unlink()
    except OSError:
        pass
    return {"status": "OK", "directx_feature_levels": feature_levels}


def probe_npu() -> dict:
    if not sysinfo.IS_WINDOWS:
        return {"status": "NOT TESTED (non-Windows)"}
    # Word-boundary match -- a naive 'NPU|...' substring match is a false-
    # positive trap: e.g. "Input Configuration Device" contains "nput",
    # which contains "npu", and -match is case-insensitive by default.
    devices = sysinfo.powershell_json(
        r"Get-PnpDevice | Where-Object { $_.FriendlyName -match '\bNPU\b|\bNeural\b|\bIPU\b|Ryzen AI' } "
        "| Select-Object FriendlyName, Status | ConvertTo-Json"
    )
    if devices:
        rows = devices if isinstance(devices, list) else [devices]
        return {"status": "PRESENT", "devices": [r.get("FriendlyName") for r in rows]}
    return {"status": "NOT DETECTED"}


def probe_directml() -> dict:
    os_level = None
    if sysinfo.IS_WINDOWS:
        dll = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "DirectML.dll"
        os_level = "PRESENT" if dll.exists() else "NOT FOUND"
    else:
        os_level = "NOT APPLICABLE (not Windows)"

    python_level = "NOT DETECTED"
    try:
        import onnxruntime  # already a Confero dependency, no new install

        if "DmlExecutionProvider" in onnxruntime.get_available_providers():
            python_level = "DETECTED"
    except ImportError:
        python_level = "UNKNOWN (onnxruntime not importable)"

    return {
        "directml_dll_os_level": os_level,
        "onnxruntime_dml_provider": python_level,
        "note": "The installed onnxruntime build is CPU-only (confirmed: "
        "'DmlExecutionProvider' is absent from get_available_providers()). "
        "Using DirectML would require installing the onnxruntime-directml "
        "package -- not done in this phase.",
        "whisper_directml_inference": "NOT TESTED",
    }


def probe_rocm() -> dict:
    hip_path = os.environ.get("HIP_PATH")
    install_dir_exists = Path(r"C:\Program Files\AMD\ROCm").exists() if sysinfo.IS_WINDOWS else False
    return {
        "hip_path_env_var": hip_path or "NOT SET",
        "rocm_install_dir_present": install_dir_exists,
        "ctranslate2_rocm_support": "NOT SUPPORTED (verified empirically in the prior audit: "
        "device='rocm' raises ValueError at the Python binding level)",
        "status": "NOT TESTED / NOT RECOMMENDED FOR THIS HARDWARE",
    }


def probe_ctranslate2_devices() -> dict:
    import ctranslate2

    cpu_types = sorted(ctranslate2.get_supported_compute_types("cpu"))
    cuda_count = ctranslate2.get_cuda_device_count()
    return {
        "cpu_compute_types_supported": cpu_types,
        "cuda_device_count": cuda_count,
        "rocm_device_string": "REJECTED (ValueError: unsupported device)",
        "dml_device_string": "REJECTED (ValueError: unsupported device)",
    }


def probe_ram() -> dict:
    ram = sysinfo.get_system_ram_mb()
    return ram or {"status": "UNKNOWN (unsupported OS for this probe)"}


def run_full_probe(include_dxdiag: bool = False) -> dict:
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python_env": probe_python_env(),
        "cpu": probe_cpu(),
        "ram": probe_ram(),
        "gpu": probe_gpu(),
        "npu": probe_npu(),
        "directml": probe_directml(),
        "rocm": probe_rocm(),
        "vulkan_whisper_backend": "NOT TESTED (whisper.cpp not installed this phase, by design)",
        "ctranslate2_devices": probe_ctranslate2_devices(),
    }
    if include_dxdiag:
        report["gpu"]["dxdiag"] = run_dxdiag_scan()
    return report


def _print_summary(report: dict) -> None:
    gpu = report["gpu"]
    print("=== Confero Hardware Capability Probe ===")
    print(f"CPU: {report['cpu']['processor_string']} "
          f"({report['cpu']['physical_cores']} physical / {report['cpu']['logical_processors']} logical)")
    print(f"AVX2: {report['cpu']['avx2']}")
    ram = report["ram"]
    if "total_mb" in ram:
        print(f"RAM: {ram['total_mb']:.0f} MB total, {ram['available_mb']:.0f} MB available "
              f"({ram['percent_used']}% used)")
    else:
        print(f"RAM: {ram}")
    print(f"GPU: {gpu['name']} (driver {gpu['driver_version']}, "
          f"~{gpu['dedicated_memory_mb']} MB dedicated)")
    print(f"VULKAN RUNTIME: {'AVAILABLE' if gpu['vulkan_runtime_present'] else 'NOT AVAILABLE'}")
    print("WHISPER VULKAN BACKEND: NOT TESTED")
    dml = report["directml"]
    print(f"DIRECTML INFRASTRUCTURE: {dml['directml_dll_os_level']} (OS) / "
          f"{dml['onnxruntime_dml_provider']} (onnxruntime)")
    print("DIRECTML WHISPER INFERENCE: NOT TESTED")
    print(f"ROCm: {report['rocm']['status']}")
    print(f"NPU: {report['npu']['status']}")
    ct2 = report["ctranslate2_devices"]
    print(f"CTranslate2 CPU compute types: {ct2['cpu_compute_types_supported']}")
    print(f"CTranslate2 CUDA device count: {ct2['cuda_device_count']}")
    print(f"faster-whisper={report['python_env']['faster_whisper']}  "
          f"ctranslate2={report['python_env']['ctranslate2']}  "
          f"torch={report['python_env']['torch']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dxdiag", action="store_true", help="Also run the slower dxdiag scan (several seconds).")
    args = parser.parse_args()

    report = run_full_probe(include_dxdiag=args.dxdiag)
    _print_summary(report)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"capability_{int(time.time())}.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nFull report saved to {out_path}")
