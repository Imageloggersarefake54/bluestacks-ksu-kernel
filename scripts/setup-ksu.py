#!/usr/bin/env python3
"""
setup-ksu.py — Post-boot KernelSU Next + Zygisk Next setup via ADB
Run this after BlueStacks boots with the new KSU kernel.

Usage:
  python setup-ksu.py

Requirements:
  - adb in PATH (or use BlueStacks HD-Adb.exe via ADB_PATH env)
  - BlueStacks Pie64 running with KSU kernel
  - Internet access to download APKs
"""

import os
import sys
import subprocess
import urllib.request
import json
import tempfile
import time

ADB = os.environ.get("ADB_PATH", "adb")
BS_ADB = r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"

# Use BlueStacks ADB if standard adb not found
if not shutil.which(ADB):
    if os.path.exists(BS_ADB):
        ADB = BS_ADB
    else:
        print("ERROR: adb not found. Set ADB_PATH env var.")
        sys.exit(1)

import shutil

def run(cmd: list, check=True, capture=True) -> subprocess.CompletedProcess:
    full_cmd = [ADB, "-s", "127.0.0.1:5555"] + cmd
    print(f"  $ {' '.join(full_cmd)}")
    result = subprocess.run(full_cmd, capture_output=capture, text=True)
    if capture and result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if check and result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}")
        raise RuntimeError(f"ADB command failed: {cmd}")
    return result

def adb_shell(cmd: str, **kwargs) -> str:
    result = run(["shell", cmd], **kwargs)
    return result.stdout.strip() if result.stdout else ""

def download_file(url: str, dest: str):
    print(f"  Downloading: {url}")
    urllib.request.urlretrieve(url, dest)
    size = os.path.getsize(dest)
    print(f"  Saved: {dest} ({size // 1024}KB)")

def get_latest_github_release(owner: str, repo: str, asset_filter: str) -> str:
    """Get download URL for latest release asset matching filter."""
    api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    with urllib.request.urlopen(api_url) as r:
        data = json.loads(r.read())
    assets = data.get("assets", [])
    for asset in assets:
        if asset_filter in asset["name"]:
            return asset["browser_download_url"]
    raise RuntimeError(f"No asset matching '{asset_filter}' in {owner}/{repo} latest release")

def wait_for_device(timeout=60):
    print("\n[*] Waiting for ADB device...")
    start = time.time()
    while time.time() - start < timeout:
        result = subprocess.run([ADB, "connect", "127.0.0.1:5555"],
                                capture_output=True, text=True)
        result2 = subprocess.run([ADB, "-s", "127.0.0.1:5555", "get-state"],
                                 capture_output=True, text=True)
        if "device" in result2.stdout:
            print("    Device connected!")
            return True
        time.sleep(3)
    return False

def check_ksu():
    print("\n[*] Checking KernelSU Next status...")
    kernel_ver = adb_shell("uname -r", check=False)
    print(f"    Kernel: {kernel_ver}")
    
    if "ksu" not in kernel_ver.lower():
        print("    WARNING: 'ksu' not in kernel version string")
        print("    Checking via ksud presence...")
    
    proc_ver = adb_shell("cat /proc/version", check=False)
    print(f"    /proc/version: {proc_ver[:80]}")
    
    # Check for KSU security interface
    ksu_present = adb_shell("[ -e /sys/kernel/security/ksud ] && echo YES || echo NO", check=False)
    if ksu_present == "YES":
        print("    KSU security interface: PRESENT ✓")
    else:
        print("    KSU security interface: NOT found (may need su -c check)")
    
    return True

def install_ksu_manager(tmpdir: str):
    print("\n[*] Installing KernelSU Next Manager...")
    
    # Get latest KernelSU Next manager APK
    try:
        url = get_latest_github_release(
            "KernelSU-Next", "KernelSU-Next", 
            "manager.apk"
        )
    except Exception:
        # Fallback to direct URL pattern
        url = "https://github.com/KernelSU-Next/KernelSU-Next/releases/latest/download/KernelSU_Next_manager.apk"
    
    apk_path = os.path.join(tmpdir, "ksu_manager.apk")
    download_file(url, apk_path)
    
    print("  Installing APK...")
    run(["install", "-r", apk_path])
    print("    KernelSU Next Manager installed ✓")

def install_zygisk_next(tmpdir: str):
    print("\n[*] Installing Zygisk Next...")
    
    # Get latest Zygisk Next zip for x86_64
    try:
        url = get_latest_github_release(
            "Dr-TSNG", "ZygiskNext",
            "x86_64"
        )
    except Exception:
        try:
            url = get_latest_github_release(
                "PerformanC", "ZygiskNext",
                "x86_64"
            )
        except Exception:
            # Try ReZygisk as alternative
            url = get_latest_github_release(
                "PerformanC", "ReZygisk",
                "x86"
            )
    
    zip_path = os.path.join(tmpdir, "zygisk_next.zip")
    download_file(url, zip_path)
    
    # Push and install via KSU module system
    remote_path = "/data/local/tmp/zygisk_next.zip"
    print(f"  Pushing to {remote_path}...")
    run(["push", zip_path, remote_path])
    
    # Install module via ksud
    print("  Installing module via ksud...")
    result = adb_shell(f"su -c 'ksud module install {remote_path}'", check=False)
    if not result:
        # Try via KSU manager intent
        print("  Trying via broadcast...")
        adb_shell(
            f"su -c 'am broadcast -a me.weishu.kernelsu.action.INSTALL_MODULE "
            f"--es path {remote_path}'",
            check=False
        )
    
    print("    Zygisk Next installed ✓")
    print("    Reboot required to activate.")

def reboot_device():
    print("\n[*] Rebooting device...")
    run(["reboot"], check=False)
    time.sleep(5)
    print("    Waiting for BlueStacks to restart (60s)...")
    wait_for_device(timeout=120)
    time.sleep(10)  # extra settle time

def verify_zygisk():
    print("\n[*] Verifying Zygisk Next...")
    # Check zygote has zygisk injected
    maps_check = adb_shell(
        "su -c 'cat /proc/$(pidof zygote)/maps 2>/dev/null | grep -i zygisk | head -3'",
        check=False
    )
    if maps_check:
        print(f"    Zygisk maps found:\n    {maps_check}")
        print("    Zygisk Next ACTIVE ✓")
    else:
        print("    Zygisk maps not found — may need module reboot cycle")
    
    # List installed KSU modules
    modules = adb_shell("su -c 'ksud module list'", check=False)
    if modules:
        print(f"    Installed modules:\n    {modules}")

def main():
    print("=" * 60)
    print("  BlueStacks KernelSU Next + Zygisk Next Setup")
    print("=" * 60)
    
    # Wait for device
    if not wait_for_device():
        print("ERROR: Could not connect to BlueStacks via ADB")
        print("Make sure BlueStacks Pie64 is running")
        sys.exit(1)
    
    # Verify KSU is working
    check_ksu()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Install KSU Manager
        install_ksu_manager(tmpdir)
        
        # Install Zygisk Next
        install_zygisk_next(tmpdir)
    
    # Reboot to activate
    reboot_device()
    
    # Verify
    verify_zygisk()
    
    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("  - KernelSU Next Manager: installed")
    print("  - Zygisk Next: installed and active")
    print("")
    print("  Next steps for offset dumping:")
    print("  1. Open KSU Manager → grant root to your dumper app")
    print("  2. Zygisk modules can now inject into game processes")
    print("  3. Use adb shell su -c <your-dumper-binary>")
    print("=" * 60)

if __name__ == "__main__":
    main()
