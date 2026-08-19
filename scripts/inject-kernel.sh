#!/usr/bin/env bash
# =============================================================================
# inject-kernel.sh  — Linux/WSL side script
# Injects a new bzImage into BlueStacks Pie64 fastboot.vdi
#
# Usage (from WSL2 or Linux):
#   chmod +x inject-kernel.sh
#   ./inject-kernel.sh /path/to/bzImage-4.19-ksu-next
#
# The script will:
#   1. Convert fastboot.vdi to raw image
#   2. Mount the boot partition
#   3. Replace vmlinuz with the new bzImage
#   4. Convert back to VDI
#   5. Copy back to Windows path
# =============================================================================

set -euo pipefail

BZIMAGE="${1:-}"
VDIPATH="/mnt/c/ProgramData/BlueStacks_nxt/Engine/Pie64/fastboot.vdi"
VDIBAK="/mnt/c/ProgramData/BlueStacks_nxt/Engine/Pie64/fastboot.vdi.bak"
RAWPATH="/tmp/fastboot_bs.raw"
MOUNTDIR="/tmp/fastboot_mount"
NEWVDI="/tmp/fastboot_ksu.vdi"

# ── Sanity checks ────────────────────────────────────────────────────────────
if [[ -z "$BZIMAGE" ]]; then
  echo "ERROR: Supply path to bzImage as first argument"
  echo "Usage: ./inject-kernel.sh /path/to/bzImage-4.19-ksu-next"
  exit 1
fi

if [[ ! -f "$BZIMAGE" ]]; then
  echo "ERROR: bzImage not found at: $BZIMAGE"
  exit 1
fi

echo "=== BlueStacks KernelSU Next Kernel Injector ==="
echo "bzImage : $BZIMAGE"
echo "VDI     : $VDIPATH"
echo ""

# ── Check dependencies ───────────────────────────────────────────────────────
for cmd in qemu-img fdisk file sudo; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Installing missing tool: $cmd"
    sudo apt-get install -y qemu-utils util-linux file &>/dev/null
    break
  fi
done

# ── Step 1: Convert VDI → raw ────────────────────────────────────────────────
echo "[1/6] Converting fastboot.vdi → raw..."
qemu-img convert -f vdi -O raw "$VDIPATH" "$RAWPATH"
echo "      Raw image: $(du -h $RAWPATH | cut -f1)"

# ── Step 2: Find partition offset ────────────────────────────────────────────
echo "[2/6] Scanning partition table..."
PART_INFO=$(sudo fdisk -l "$RAWPATH" 2>/dev/null | grep "^$RAWPATH" | head -1)
echo "      $PART_INFO"

# Extract start sector and sector size
SECTOR_SIZE=$(sudo fdisk -l "$RAWPATH" | grep "Sector size" | awk '{print $4}')
START_SECTOR=$(echo "$PART_INFO" | awk '{print $2}')

if [[ -z "$START_SECTOR" ]] || [[ "$START_SECTOR" == "*" ]]; then
  # Try second field if first is the boot marker
  START_SECTOR=$(echo "$PART_INFO" | awk '{print $3}')
fi

OFFSET=$(( START_SECTOR * SECTOR_SIZE ))
echo "      Sector size: $SECTOR_SIZE  Start sector: $START_SECTOR  Offset: $OFFSET bytes"

# ── Step 3: Mount the partition ──────────────────────────────────────────────
echo "[3/6] Mounting boot partition..."
sudo mkdir -p "$MOUNTDIR"
sudo mount -o loop,offset="$OFFSET" "$RAWPATH" "$MOUNTDIR"

# List boot contents
echo "      Contents of /boot:"
ls -lh "$MOUNTDIR/boot/" 2>/dev/null || ls -lh "$MOUNTDIR/" 2>/dev/null

# Find the kernel file
KERNEL_FILE=""
for candidate in \
  "$MOUNTDIR/boot/vmlinuz" \
  "$MOUNTDIR/boot/bzImage" \
  "$MOUNTDIR/vmlinuz" \
  "$MOUNTDIR/bzImage"; do
  if [[ -f "$candidate" ]]; then
    KERNEL_FILE="$candidate"
    break
  fi
done

if [[ -z "$KERNEL_FILE" ]]; then
  echo "ERROR: Could not find kernel file inside VDI partition"
  echo "Available files:"
  find "$MOUNTDIR" -type f | head -20
  sudo umount "$MOUNTDIR"
  exit 1
fi

echo "      Found kernel: $KERNEL_FILE ($(du -h $KERNEL_FILE | cut -f1))"

# ── Step 4: Replace kernel ───────────────────────────────────────────────────
echo "[4/6] Replacing kernel..."
# Verify the new bzImage is valid
file "$BZIMAGE"
strings "$BZIMAGE" | grep "Linux version" | head -1

sudo cp "$BZIMAGE" "$KERNEL_FILE"
echo "      New kernel: $(du -h $KERNEL_FILE | cut -f1)"
strings "$KERNEL_FILE" | grep "Linux version" | head -1

# ── Step 5: Unmount and convert back ─────────────────────────────────────────
echo "[5/6] Unmounting and converting back to VDI..."
sync
sudo umount "$MOUNTDIR"

qemu-img convert -f raw -O vdi "$RAWPATH" "$NEWVDI"
echo "      New VDI: $(du -h $NEWVDI | cut -f1)"

# ── Step 6: Copy back ────────────────────────────────────────────────────────
echo "[6/6] Writing new VDI back to BlueStacks..."
echo "      (BlueStacks must be fully stopped for this to work)"

# Double-check backup exists
if [[ ! -f "$VDIBAK" ]]; then
  echo "      WARNING: Backup not found at $VDIBAK — making one now"
  cp "$VDIPATH" "$VDIBAK"
fi

cp "$NEWVDI" "$VDIPATH"

echo ""
echo "=== DONE ==="
echo "New kernel injected. Start BlueStacks Pie64 and verify:"
echo ""
echo "  adb connect 127.0.0.1:5555"
echo "  adb shell uname -r          # should show 4.19.x-ksu-next"
echo "  adb shell cat /proc/version"
echo ""
echo "If BlueStacks fails to boot, restore backup:"
echo "  cp '$VDIBAK' '$VDIPATH'"
echo ""
