#!/usr/bin/env python3
"""
automate.py — Full end-to-end automation script
BlueStacks KernelSU Next build, inject, and setup

Handles:
  1. GitHub auth (device flow — one browser click)
  2. Create GitHub repo
  3. Push build pipeline
  4. Poll Actions until kernel build completes
  5. Download bzImage artifact
  6. Run PowerShell kernel injector (as admin via UAC)
  7. Run KSU+Zygisk ADB setup

Usage:
  python automate.py
"""

import os, sys, subprocess, time, json, zipfile, shutil, webbrowser, tempfile
import urllib.request, urllib.error, urllib.parse

# ── Config ────────────────────────────────────────────────────────────────────
REPO_NAME   = "bluestacks-ksu-kernel"
REPO_DESC   = "BlueStacks Pie64 KernelSU Next kernel build"
BRANCH      = "master"
WORKFLOW    = "build-kernel.yml"
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BS_ADB      = r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe"

GITHUB_API  = "https://api.github.com"

# ── Colors ────────────────────────────────────────────────────────────────────
class C:
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"

def info(msg):    print(f"{C.CYAN}[*]{C.RESET} {msg}")
def ok(msg):      print(f"{C.GREEN}[+]{C.RESET} {msg}")
def warn(msg):    print(f"{C.YELLOW}[!]{C.RESET} {msg}")
def err(msg):     print(f"{C.RED}[X]{C.RESET} {msg}"); sys.exit(1)
def step(n, msg): print(f"\n{C.MAGENTA}{C.BOLD}[{n}]{C.RESET} {C.BOLD}{msg}{C.RESET}")

# ── GitHub API helpers ────────────────────────────────────────────────────────
def gh_request(method: str, path: str, token: str, data=None, expected=None):
    url = f"{GITHUB_API}{path}" if not path.startswith("http") else path
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "ksu-builder/1.0"
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if expected and e.code in expected:
            return {"error": e.code, "body": body}
        raise RuntimeError(f"GitHub API {method} {path} -> {e.code}: {body[:200]}")

# ── Auth via device flow ──────────────────────────────────────────────────────
def github_auth() -> str:
    """Authenticate with GitHub using device flow. Returns access token."""
    
    CLIENT_ID = "Iv1.b507a08c87ecfe98"  # GitHub CLI app client ID (public)
    
    info("Starting GitHub device flow authentication...")
    
    # Step 1: request device code
    data = urllib.parse.urlencode({"client_id": CLIENT_ID, "scope": "repo,workflow"}).encode()
    req = urllib.request.Request(
        "https://github.com/login/device/code",
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())
    
    device_code     = resp["device_code"]
    user_code       = resp["user_code"]
    verification_url = resp["verification_uri"]
    interval        = resp.get("interval", 5)
    expires_in      = resp.get("expires_in", 900)
    
    print()
    print(f"  {C.BOLD}{C.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C.RESET}")
    print(f"  {C.BOLD}GitHub Auth — one step needed from you:{C.RESET}")
    print()
    print(f"  1. Open: {C.CYAN}{verification_url}{C.RESET}")
    print(f"  2. Enter code: {C.BOLD}{C.GREEN}{user_code}{C.RESET}")
    print(f"  {C.BOLD}{C.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C.RESET}")
    print()
    
    # Auto-open browser
    webbrowser.open(verification_url)
    
    # Step 2: poll for token
    info("Waiting for you to authorize in browser...")
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        
        poll_data = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
        }).encode()
        
        poll_req = urllib.request.Request(
            "https://github.com/login/oauth/access_token",
            data=poll_data,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(poll_req) as r:
            poll_resp = json.loads(r.read())
        
        if "access_token" in poll_resp:
            token = poll_resp["access_token"]
            user = gh_request("GET", "/user", token)["login"]
            ok(f"Authenticated as: {user}")
            return token, user
        
        error = poll_resp.get("error", "")
        if error == "authorization_pending":
            sys.stdout.write(".")
            sys.stdout.flush()
            continue
        elif error == "slow_down":
            interval += 5
            continue
        elif error in ("expired_token", "access_denied"):
            err(f"Auth failed: {error}")
    
    err("Auth timed out. Re-run and authorize within 15 minutes.")

# ── Create GitHub repo ────────────────────────────────────────────────────────
def create_repo(token: str, username: str) -> str:
    info(f"Creating GitHub repo: {username}/{REPO_NAME}")
    
    # Check if already exists
    existing = gh_request("GET", f"/repos/{username}/{REPO_NAME}", token, expected=[404])
    if "error" not in existing:
        warn(f"Repo already exists: {existing['html_url']}")
        return existing["html_url"], existing["clone_url"]
    
    result = gh_request("POST", "/user/repos", token, {
        "name": REPO_NAME,
        "description": REPO_DESC,
        "private": False,
        "auto_init": False
    })
    ok(f"Repo created: {result['html_url']}")
    return result["html_url"], result["clone_url"]

# ── Push code via git ─────────────────────────────────────────────────────────
def push_code(token: str, username: str, clone_url: str):
    info("Pushing build pipeline to GitHub...")
    
    # Embed token in URL for auth
    auth_url = clone_url.replace("https://", f"https://{username}:{token}@")
    
    git = lambda *args: subprocess.run(
        ["git"] + list(args),
        cwd=PROJECT_DIR,
        capture_output=True, text=True
    )
    
    # Set remote
    git("remote", "remove", "origin")
    git("remote", "add", "origin", auth_url)
    
    # Push
    result = git("push", "-u", "origin", BRANCH, "--force")
    if result.returncode != 0:
        print(result.stderr)
        err("git push failed")
    
    ok(f"Code pushed to {BRANCH} branch")

# ── Wait for workflow run ─────────────────────────────────────────────────────
def wait_for_build(token: str, username: str) -> dict:
    info(f"Waiting for GitHub Actions build to complete...")
    info("(This typically takes 15–25 minutes for a Linux kernel)")
    
    repo = f"{username}/{REPO_NAME}"
    run_id = None
    
    # Wait for workflow run to appear
    for _ in range(60):
        time.sleep(5)
        runs = gh_request("GET", f"/repos/{repo}/actions/runs?branch={BRANCH}&per_page=5", token)
        for run in runs.get("workflow_runs", []):
            if WORKFLOW in run.get("path", "") or WORKFLOW in run.get("name", ""):
                run_id = run["id"]
                break
        if run_id:
            ok(f"Found workflow run #{run_id}")
            break
        sys.stdout.write("⏳")
        sys.stdout.flush()
    
    if not run_id:
        err("Workflow run not found. Check Actions tab in GitHub.")
    
    # Poll until complete
    start = time.time()
    last_status = ""
    while True:
        run = gh_request("GET", f"/repos/{repo}/actions/runs/{run_id}", token)
        status     = run["status"]
        conclusion = run.get("conclusion")
        elapsed    = int(time.time() - start)
        
        status_str = f"  [{elapsed//60:02d}m{elapsed%60:02d}s] Status: {status}"
        if status != last_status:
            print(f"\n{status_str}", end="")
            last_status = status
        else:
            sys.stdout.write(".")
            sys.stdout.flush()
        
        if status == "completed":
            print()
            if conclusion == "success":
                ok(f"Build completed successfully! ({elapsed//60}min {elapsed%60}s)")
                return run
            else:
                err(f"Build {conclusion}. Check: {run['html_url']}")
        
        time.sleep(20)

# ── Download artifact ─────────────────────────────────────────────────────────
def download_artifact(token: str, username: str, run: dict, dest_dir: str) -> str:
    repo = f"{username}/{REPO_NAME}"
    run_id = run["id"]
    
    info("Fetching build artifacts...")
    artifacts = gh_request("GET", f"/repos/{repo}/actions/runs/{run_id}/artifacts", token)
    
    target = None
    for art in artifacts.get("artifacts", []):
        if "ksu" in art["name"].lower() or "kernel" in art["name"].lower():
            target = art
            break
    
    if not target:
        err(f"No kernel artifact found. Available: {[a['name'] for a in artifacts.get('artifacts', [])]}")
    
    ok(f"Artifact: {target['name']} ({target['size_in_bytes'] // 1024}KB)")
    
    # Download
    info("Downloading artifact zip...")
    zip_path = os.path.join(dest_dir, "artifact.zip")
    
    # GitHub redirects artifact downloads — need to handle redirect
    req = urllib.request.Request(
        target["archive_download_url"],
        headers={"Authorization": f"Bearer {token}", "User-Agent": "ksu-builder/1.0"}
    )
    with urllib.request.urlopen(req) as r:
        with open(zip_path, "wb") as f:
            f.write(r.read())
    
    # Extract
    extract_dir = os.path.join(dest_dir, "artifact_extracted")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
    
    # Find bzImage
    bzimage = None
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if "bzImage" in f or f == "vmlinuz":
                bzimage = os.path.join(root, f)
                break
    
    if not bzimage:
        err(f"bzImage not found in artifact. Contents: {os.listdir(extract_dir)}")
    
    ok(f"bzImage: {bzimage} ({os.path.getsize(bzimage) // (1024*1024)}MB)")
    return bzimage

# ── Inject kernel (elevated PS) ───────────────────────────────────────────────
def inject_kernel(bzimage: str):
    info("Injecting kernel into BlueStacks fastboot.vdi...")
    warn("This requires Administrator privileges — Windows UAC prompt will appear.")
    
    ps_script = os.path.join(SCRIPTS_DIR, "inject-kernel.ps1")
    bzimage_win = bzimage.replace("/", "\\")
    
    # Launch elevated PowerShell
    result = subprocess.run([
        "powershell", "-Command",
        f"Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"{ps_script}\" -BzImage \"{bzimage_win}\"' -Verb RunAs -Wait"
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        warn(f"Elevation returned: {result.returncode}")
        warn("If injection failed, run manually as Admin:")
        warn(f'  powershell -ExecutionPolicy Bypass -File "{ps_script}" -BzImage "{bzimage_win}"')
    else:
        ok("Kernel injection complete!")

# ── Verify boot + setup KSU ───────────────────────────────────────────────────
def post_boot_setup():
    info("Starting KSU Manager + Zygisk Next setup...")
    info("Make sure BlueStacks Pie64 is running first!")
    
    # Run setup-ksu.py
    setup_script = os.path.join(SCRIPTS_DIR, "setup-ksu.py")
    result = subprocess.run(
        [sys.executable, setup_script],
        cwd=SCRIPTS_DIR
    )
    if result.returncode != 0:
        warn("setup-ksu.py exited non-zero. Check ADB connection and retry.")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print()
    print(f"{C.MAGENTA}{C.BOLD}╔══════════════════════════════════════════════════════╗{C.RESET}")
    print(f"{C.MAGENTA}{C.BOLD}║   BlueStacks KernelSU Next — Full Automation         ║{C.RESET}")
    print(f"{C.MAGENTA}{C.BOLD}║   Linux 4.19.195 x86_64 + KSU Next Legacy            ║{C.RESET}")
    print(f"{C.MAGENTA}{C.BOLD}╚══════════════════════════════════════════════════════╝{C.RESET}")
    print()

    with tempfile.TemporaryDirectory() as tmpdir:
        # ── Auth ──────────────────────────────────────────────────────────────
        step(1, "GitHub Authentication")
        token, username = github_auth()
        
        # ── Create repo ───────────────────────────────────────────────────────
        step(2, "Create GitHub Repository")
        repo_url, clone_url = create_repo(token, username)
        print(f"  → {repo_url}")
        
        # ── Push code ─────────────────────────────────────────────────────────
        step(3, "Push Build Pipeline")
        push_code(token, username, clone_url)
        
        # ── Wait for build ────────────────────────────────────────────────────
        step(4, "Build Kernel (GitHub Actions)")
        print(f"  Monitor at: {repo_url}/actions")
        run = wait_for_build(token, username)
        
        # ── Download artifact ─────────────────────────────────────────────────
        step(5, "Download bzImage Artifact")
        bzimage = download_artifact(token, username, run, tmpdir)
        
        # ── Inject kernel ──────────────────────────────────────────────────────
        step(6, "Inject Kernel into BlueStacks")
        inject_kernel(bzimage)
        
        # ── Post-boot setup ───────────────────────────────────────────────────
        step(7, "KSU Manager + Zygisk Next Setup")
        input("\n  Start BlueStacks (Pie64 instance) then press ENTER to continue...")
        post_boot_setup()
    
    print()
    print(f"{C.GREEN}{C.BOLD}╔══════════════════════════════════════════╗{C.RESET}")
    print(f"{C.GREEN}{C.BOLD}║  ALL DONE. KernelSU Next is live.        ║{C.RESET}")
    print(f"{C.GREEN}{C.BOLD}║  Zygisk Next is active in Pie64.         ║{C.RESET}")
    print(f"{C.GREEN}{C.BOLD}╚══════════════════════════════════════════╝{C.RESET}")
    print()

if __name__ == "__main__":
    main()
