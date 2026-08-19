# BlueStacks KernelSU Next — Build Pipeline

Builds **Linux 4.19.195 x86_64** with **KernelSU Next (legacy driver)** for BlueStacks 5 Pie64.

## What this does

| Step | Tool | Where |
|------|------|--------|
| Build kernel | GitHub Actions (Ubuntu runner) | Cloud |
| Download bzImage | GitHub Actions artifact | Your PC |
| Inject into fastboot.vdi | `inject-kernel.sh` | WSL2 |
| Install KSU Manager + Zygisk | `setup-ksu.py` | Windows (Python) |

---

## Step 1 — Push to GitHub and trigger the build

```bash
# 1. Create a new GitHub repo (public or private)
#    https://github.com/new

# 2. Push this directory
git init
git add .
git commit -m "init: BlueStacks KSU Next kernel build"
git remote add origin https://github.com/YOUR_USERNAME/bluestacks-ksu-kernel.git
git push -u origin main

# 3. GitHub Actions auto-triggers on push
#    Go to: https://github.com/YOUR_USERNAME/bluestacks-ksu-kernel/actions
#    Watch the "Build BlueStacks KernelSU Next Kernel" workflow
#    Build takes ~15-25 minutes
```

## Step 2 — Download the artifact

1. Go to your repo's **Actions** tab
2. Click the completed workflow run
3. Scroll to **Artifacts** at the bottom
4. Download `bluestacks-ksu-next-kernel-v4.19.195`
5. Extract it — you want `bzImage-4.19-ksu-next`

## Step 3 — Inject kernel (requires WSL2)

If you don't have WSL2:
```powershell
# Install WSL2 (requires restart)
wsl --install -d Ubuntu
```

After WSL2 is ready:
```bash
# Make sure BlueStacks is completely stopped first!
# In WSL2:
chmod +x scripts/inject-kernel.sh
./scripts/inject-kernel.sh /mnt/c/Users/YOUR_USER/Downloads/bzImage-4.19-ksu-next
```

## Step 4 — Boot and verify

1. Start BlueStacks → open Pie64 instance
2. Via ADB:
```powershell
& "C:\Program Files\BlueStacks_nxt\HD-Adb.exe" connect 127.0.0.1:5555
& "C:\Program Files\BlueStacks_nxt\HD-Adb.exe" -s 127.0.0.1:5555 shell uname -r
# Expected: 4.19.195-ksu-next or similar
```

## Step 5 — Install KSU Manager + Zygisk Next

```powershell
python scripts\setup-ksu.py
```

---

## Restore if something breaks

```powershell
# Stop BlueStacks first, then:
Copy-Item "C:\ProgramData\BlueStacks_nxt\Engine\Pie64\fastboot.vdi.bak" `
          "C:\ProgramData\BlueStacks_nxt\Engine\Pie64\fastboot.vdi" -Force
```

---

## Files

```
.github/workflows/build-kernel.yml   — CI build pipeline
config/bluestacks_pie64_defconfig    — Kernel config (x86_64, KSU, kprobes)
scripts/inject-kernel.sh             — VDI kernel injector (WSL2)
scripts/setup-ksu.py                 — Post-boot KSU+Zygisk installer
```

## Kernel config highlights

- `CONFIG_KSU=y` — KernelSU Next driver
- `CONFIG_KSU_KPROBE_HOOKS=y` — kprobe-based su hooks
- `CONFIG_KPROBES=y` + `CONFIG_KPROBE_EVENTS=y`
- `CONFIG_KALLSYMS_ALL=y` — needed for symbol resolution
- `CONFIG_ANDROID_BINDER_IPC=y` — Android binder
- `CONFIG_SECURITY_SELINUX=y` — SELinux (permissive mode via boot param)
- VirtIO drivers for BlueStacks network/block
