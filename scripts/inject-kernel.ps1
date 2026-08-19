# =============================================================================
# inject-kernel.ps1 — Windows PowerShell native kernel injector
# No WSL required. Uses Windows native VHD mounting + qemu-img for conversion.
#
# Prerequisites:
#   - qemu-img.exe (from QEMU for Windows, or we download it)
#   - 7-Zip (optional, for backup)
#   - Run as ADMINISTRATOR (required for Mount-DiskImage)
#
# Usage (in elevated PowerShell):
#   .\inject-kernel.ps1 -BzImage "C:\Users\you\Downloads\bzImage-4.19-ksu-next"
# =============================================================================

param(
    [Parameter(Mandatory=$true)]
    [string]$BzImage,
    
    [string]$VdiPath = "C:\ProgramData\BlueStacks_nxt\Engine\Pie64\fastboot.vdi",
    [string]$TempDir = "$env:TEMP\ksu_inject",
    [switch]$SkipQemuDownload
)

$ErrorActionPreference = "Stop"

# ── Colors ────────────────────────────────────────────────────────────────────
function Info($msg)    { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Success($msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg)    { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Err($msg)     { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "║   BlueStacks KernelSU Next — Kernel Injector (PS)   ║" -ForegroundColor Magenta
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host ""

# ── Check admin ───────────────────────────────────────────────────────────────
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Err "Run this script as Administrator (right-click PowerShell -> Run as administrator)"
}

# ── Validate inputs ──────────────────────────────────────────────────────────
if (-not (Test-Path $BzImage)) { Err "bzImage not found: $BzImage" }
if (-not (Test-Path $VdiPath)) { Err "fastboot.vdi not found: $VdiPath" }

Info "bzImage : $BzImage"
Info "VDI     : $VdiPath"
Write-Host ""

# ── Create temp dir ───────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
$Vhd = "$TempDir\fastboot.vhd"
$VhdNew = "$TempDir\fastboot_ksu.vhd"
$VdiNew = "$TempDir\fastboot_ksu.vdi"
$BackupVdi = "$VdiPath.bak"

# ── Find or download qemu-img ─────────────────────────────────────────────────
$qemuImg = $null

# Check common locations
$qemuPaths = @(
    "C:\Program Files\qemu\qemu-img.exe",
    "C:\Program Files (x86)\qemu\qemu-img.exe",
    "$env:ProgramFiles\qemu\qemu-img.exe",
    (Get-Command "qemu-img" -ErrorAction SilentlyContinue)?.Source
)

foreach ($p in $qemuPaths) {
    if ($p -and (Test-Path $p)) { $qemuImg = $p; break }
}

if (-not $qemuImg -and -not $SkipQemuDownload) {
    Info "qemu-img not found. Downloading standalone qemu-img.exe..."
    
    # Download standalone qemu-img for Windows (from qemu.weilnetz.de)
    $qemuUrl = "https://qemu.weilnetz.de/w64/2024/qemu-w64-setup-20240220.exe"
    $qemuSetup = "$TempDir\qemu-setup.exe"
    
    Warn "Downloading QEMU installer (~80MB)..."
    Invoke-WebRequest -Uri $qemuUrl -OutFile $qemuSetup -UseBasicParsing
    
    Info "Installing QEMU silently..."
    Start-Process -FilePath $qemuSetup -ArgumentList "/S" -Wait
    
    $qemuImg = "C:\Program Files\qemu\qemu-img.exe"
    if (-not (Test-Path $qemuImg)) {
        Err "QEMU installation failed. Install manually from https://qemu.weilnetz.de/w64/"
    }
}

if (-not $qemuImg) {
    Err "qemu-img.exe not found. Install QEMU from https://qemu.weilnetz.de/w64/ and re-run."
}

Success "qemu-img: $qemuImg"

# ── Check BlueStacks is stopped ───────────────────────────────────────────────
$bsProcs = Get-Process -Name "HD-Player", "BstkVMMgr", "BstkSVC" -ErrorAction SilentlyContinue
if ($bsProcs) {
    Warn "BlueStacks is RUNNING. Stop it first, then re-run this script."
    Warn "Processes found: $($bsProcs.Name -join ', ')"
    $confirm = Read-Host "Force continue anyway? (NOT RECOMMENDED) [y/N]"
    if ($confirm -ne "y") { exit 1 }
}

# ── Step 1: Backup ───────────────────────────────────────────────────────────
Info "[1/6] Backing up original fastboot.vdi..."
if (-not (Test-Path $BackupVdi)) {
    Copy-Item $VdiPath $BackupVdi -Force
    Success "Backup: $BackupVdi ($(([System.IO.FileInfo]$BackupVdi).Length / 1MB)MB)"
} else {
    Success "Backup already exists: $BackupVdi"
}

# ── Step 2: Convert VDI → VHD ────────────────────────────────────────────────
Info "[2/6] Converting VDI → VHD (Windows-mountable format)..."
& $qemuImg convert -f vdi -O vpc $VdiPath $Vhd
if ($LASTEXITCODE -ne 0) { Err "qemu-img conversion failed" }
$vhdSize = ([System.IO.FileInfo]$Vhd).Length
Success "VHD created: $Vhd ($([Math]::Round($vhdSize/1MB, 2))MB)"

# ── Step 3: Mount VHD ─────────────────────────────────────────────────────────
Info "[3/6] Mounting VHD as Windows volume..."
$disk = Mount-DiskImage -ImagePath $Vhd -PassThru
Start-Sleep -Seconds 2
$diskNum = ($disk | Get-DiskImage).Number
$partition = Get-Partition -DiskNumber $diskNum | Where-Object { $_.Type -ne "Reserved" } | Select-Object -First 1

if (-not $partition) {
    Dismount-DiskImage -ImagePath $Vhd
    Err "No accessible partition found in VHD"
}

# Assign drive letter if not already assigned
if (-not $partition.DriveLetter) {
    Add-PartitionAccessPath -DiskNumber $diskNum -PartitionNumber $partition.PartitionNumber -AssignDriveLetter
    Start-Sleep -Seconds 1
    $partition = Get-Partition -DiskNumber $diskNum -PartitionNumber $partition.PartitionNumber
}

$driveLetter = $partition.DriveLetter
if (-not $driveLetter) {
    Dismount-DiskImage -ImagePath $Vhd
    Err "Could not assign drive letter to VHD partition"
}

$mountPath = "$($driveLetter):"
Info "Mounted at: $mountPath"

# List contents
Write-Host ""
Write-Host "  Contents of $mountPath" -ForegroundColor DarkCyan
Get-ChildItem $mountPath -Recurse -Depth 2 -ErrorAction SilentlyContinue | 
    Format-Table Name, Length -AutoSize

# ── Step 4: Find and replace kernel ───────────────────────────────────────────
Info "[4/6] Locating kernel in mounted VHD..."
$kernelCandidates = @(
    "$mountPath\boot\vmlinuz",
    "$mountPath\boot\bzImage",
    "$mountPath\vmlinuz",
    "$mountPath\bzImage"
)

$kernelFile = $null
foreach ($c in $kernelCandidates) {
    if (Test-Path $c) { $kernelFile = $c; break }
}

if (-not $kernelFile) {
    # Try to find any file that looks like a kernel (ELF magic or bzImage magic)
    $kernelFile = Get-ChildItem $mountPath -Recurse -ErrorAction SilentlyContinue | 
        Where-Object { $_.Length -gt 3MB -and $_.Length -lt 30MB } |
        Select-Object -First 1 -ExpandProperty FullName
    
    if ($kernelFile) {
        Warn "Found possible kernel at: $kernelFile"
    } else {
        Dismount-DiskImage -ImagePath $Vhd
        Err "Could not find kernel file in VHD. Contents listed above."
    }
}

$origSize = ([System.IO.FileInfo]$kernelFile).Length
Success "Found kernel: $kernelFile ($([Math]::Round($origSize/1MB, 2))MB)"

Info "Replacing with KSU kernel..."
Copy-Item $BzImage $kernelFile -Force
$newSize = ([System.IO.FileInfo]$kernelFile).Length
Success "New kernel: $kernelFile ($([Math]::Round($newSize/1MB, 2))MB)"

# ── Step 5: Dismount and convert back ─────────────────────────────────────────
Info "[5/6] Dismounting VHD..."
# Remove drive letter assignment
Remove-PartitionAccessPath -DiskNumber $diskNum -PartitionNumber $partition.PartitionNumber -AccessPath "$mountPath\" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Dismount-DiskImage -ImagePath $Vhd
Start-Sleep -Seconds 1

Info "Converting VHD → VDI..."
& $qemuImg convert -f vpc -O vdi $Vhd $VdiNew
if ($LASTEXITCODE -ne 0) { Err "qemu-img back-conversion failed" }
$newVdiSize = ([System.IO.FileInfo]$VdiNew).Length
Success "New VDI: $VdiNew ($([Math]::Round($newVdiSize/1MB, 2))MB)"

# ── Step 6: Replace in BlueStacks ─────────────────────────────────────────────
Info "[6/6] Replacing fastboot.vdi in BlueStacks..."
Copy-Item $VdiNew $VdiPath -Force
Success "Done! fastboot.vdi replaced with KSU Next kernel."

# ── Cleanup ───────────────────────────────────────────────────────────────────
Remove-Item $Vhd -Force -ErrorAction SilentlyContinue
Remove-Item $VhdNew -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║  Kernel injection complete! DONE         ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "  1. Start BlueStacks -> Pie64 instance" -ForegroundColor White
Write-Host "  2. Run in PowerShell:" -ForegroundColor White
Write-Host '       & "C:\Program Files\BlueStacks_nxt\HD-Adb.exe" connect 127.0.0.1:5555' -ForegroundColor Yellow
Write-Host '       & "C:\Program Files\BlueStacks_nxt\HD-Adb.exe" -s 127.0.0.1:5555 shell uname -r' -ForegroundColor Yellow
Write-Host "  3. Should show: 4.19.xxx-ksu-next" -ForegroundColor White
Write-Host "  4. Run: python scripts\setup-ksu.py" -ForegroundColor White
Write-Host ""
Write-Host "  Restore backup if needed:" -ForegroundColor DarkGray
Write-Host "    Copy-Item '$BackupVdi' '$VdiPath' -Force" -ForegroundColor DarkGray
Write-Host ""
