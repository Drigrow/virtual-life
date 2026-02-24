#!/usr/bin/env python3
"""
Virtual Life — Interactive Setup Script
Run this once to configure your environment, then use it to start the app.

  Linux/macOS : python3 setup.py
  Windows     : python setup.py
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/Drigrow/virtual-life"

# ── Colour helpers (gracefully degrade on Windows without ANSI) ──────────────
_USE_COLOUR = sys.stdout.isatty() and platform.system() != "Windows" or (
    platform.system() == "Windows"
    and os.environ.get("TERM_PROGRAM") in ("vscode", "mintty", "xterm")
)

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def bold(t):   return _c("1", t)
def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def red(t):    return _c("31", t)
def cyan(t):   return _c("36", t)
def dim(t):    return _c("2", t)

# ── Helpers ───────────────────────────────────────────────────────────────────

def hr(char="─", width=60):
    print(dim(char * width))

def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """Prompt the user; return default on empty input."""
    hint = f" [{dim(default)}]" if default and not secret else ""
    full_prompt = f"  {cyan('?')} {prompt}{hint}: "
    try:
        if secret:
            import getpass
            val = getpass.getpass(full_prompt)
        else:
            val = input(full_prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val.strip() or default

def ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = ask(f"{prompt} ({hint})", default="")
    if not raw:
        return default
    return raw.lower().startswith("y")

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kwargs)

def pip_install(venv_python: Path, *packages: str) -> None:
    run([str(venv_python), "-m", "pip", "install", "--quiet", "--upgrade", *packages])

# ── Banner ────────────────────────────────────────────────────────────────────

def print_banner():
    print()
    print(bold("  ╔══════════════════════════════════════════╗"))
    print(bold("  ║        Virtual Life  —  Setup            ║"))
    print(bold("  ╚══════════════════════════════════════════╝"))
    print()
    print(yellow("  ⚠  WARNING: This app is designed for LOCAL personal use only."))
    print(yellow("     Do NOT expose it to the public internet or a production"))
    print(yellow("     environment. There is no rate-limiting on the chat endpoint"))
    print(yellow("     and your API key / personal data could be at risk."))
    print()
    hr()

# ── Repo detection & download ─────────────────────────────────────────────────

def ensure_repo(script_dir: Path) -> Path:
    """If the source code isn't present next to setup.py, fetch it first.

    Strategy:
      1. main.py already exists → we're inside the repo, do nothing.
      2. git is available → git clone.
      3. No git → download the ZIP from GitHub using stdlib urllib (no deps needed).

    Returns the directory that contains main.py.
    """
    if (script_dir / "main.py").exists():
        return script_dir  # already inside the repo — nothing to do

    print()
    print(yellow("  Source code not found next to this script."))
    print(yellow("  Virtual Life will be downloaded from GitHub."))
    print()
    print(f"  {dim('Repo')}: {REPO_URL}")
    print()

    clone_name = ask("Download into folder name", default="virtual-life")
    clone_target = script_dir / clone_name

    # ── Reuse existing folder if it already has the code ─────────────────────
    if clone_target.exists() and any(clone_target.iterdir()):
        print(f"  {yellow('!')} {clone_target} already exists and is non-empty.")
        if (clone_target / "main.py").exists():
            print(f"  {green('✓')} main.py found — using existing folder.")
            return _relaunch_from(clone_target)
        else:
            print(red("  main.py not found inside that folder. Please remove it and retry."))
            sys.exit(1)

    # ── Try git first (cleanest, preserves history) ───────────────────────────
    if shutil.which("git"):
        print(f"  {green('git')} found — cloning repo…")
        try:
            run(["git", "clone", REPO_URL, str(clone_target)])
            print(f"  {green('✓')} Clone complete.")
            return _relaunch_from(clone_target)
        except subprocess.CalledProcessError:
            print(yellow("  git clone failed — falling back to ZIP download…"))
            # Clean up partial clone if any
            if clone_target.exists():
                shutil.rmtree(clone_target, ignore_errors=True)
    else:
        print(f"  {dim('git not found')} — downloading ZIP instead (no git required).")

    # ── Fallback: download ZIP from GitHub (stdlib only, no extra deps) ───────
    import tempfile
    import urllib.request
    import zipfile

    zip_url = f"{REPO_URL}/archive/refs/heads/main.zip"
    print(f"  Downloading {dim(zip_url)} …")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_zip = Path(tmp) / "repo.zip"

            # Stream with a simple progress bar
            def _reporthook(count, block_size, total_size):
                if total_size > 0:
                    pct = min(100, count * block_size * 100 // total_size)
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    print(f"\r  [{bar}] {pct}% ", end="", flush=True)

            urllib.request.urlretrieve(zip_url, tmp_zip, reporthook=_reporthook)
            print(f"\r  {green('✓')} Download complete.                          ")

            # Extract — GitHub ZIPs have a top-level folder like "virtual-life-main/"
            print("  Extracting…", end="", flush=True)
            with zipfile.ZipFile(tmp_zip, "r") as zf:
                zf.extractall(tmp)
            print(f"\r  {green('✓')} Extracted.   ")

            # Find the extracted top-level directory
            extracted_dirs = [p for p in Path(tmp).iterdir() if p.is_dir()]
            if not extracted_dirs:
                print(red("  ZIP extraction produced no folders. The download may be corrupt."))
                sys.exit(1)

            extracted = extracted_dirs[0]
            print(f"  Moving to {dim(str(clone_target))} …")
            shutil.move(str(extracted), str(clone_target))
            print(f"  {green('✓')} Ready at {dim(str(clone_target))}")

    except Exception as exc:
        print(red(f"\n  Download failed: {exc}"))
        print(red(f"  Please download manually: {REPO_URL}/archive/refs/heads/main.zip"))
        sys.exit(1)

    return _relaunch_from(clone_target)


def _relaunch_from(repo_dir: Path) -> Path:
    """Re-exec setup.py from inside the repo dir so all paths are correct."""
    new_setup = repo_dir / "setup.py"
    if not new_setup.exists():
        # setup.py not in repo yet (edge case) — just chdir and continue
        os.chdir(repo_dir)
        return repo_dir

    print()
    print(f"  {cyan('↻')} Continuing setup inside {dim(str(repo_dir))}…")
    print()
    # os.execv replaces the current process entirely — seamless to the user
    os.execv(sys.executable, [sys.executable, str(new_setup)])
    sys.exit(0)  # unreachable

# ── System detection ──────────────────────────────────────────────────────────

def detect_system() -> dict:
    system = platform.system()          # 'Linux', 'Darwin', 'Windows'
    is_linux   = system == "Linux"
    is_mac     = system == "Darwin"
    is_windows = system == "Windows"

    py_version = sys.version_info
    py_ok = py_version >= (3, 10)

    print(f"  {bold('System')}  : {system} {platform.release()}")
    print(f"  {bold('Python')}  : {platform.python_version()} {'✓' if py_ok else red('✗ need ≥ 3.10')}")
    print()

    if not py_ok:
        print(red("  Python 3.10+ is required. Please upgrade and re-run this script."))
        sys.exit(1)

    return {"system": system, "is_linux": is_linux, "is_mac": is_mac, "is_windows": is_windows}

# ── Linux: ensure python3-venv is available ───────────────────────────────────

def ensure_linux_venv_package():
    """On Debian/Ubuntu, python3-venv may not be installed."""
    try:
        run([sys.executable, "-m", "venv", "--help"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return  # already works
    except subprocess.CalledProcessError:
        pass

    print(yellow("  python3-venv not found — attempting to install via apt…"))
    try:
        run(["sudo", "apt-get", "install", "-y", "python3-venv"])
        print(green("  python3-venv installed."))
    except Exception:
        print(red("  Could not auto-install python3-venv."))
        print(red("  Run:  sudo apt-get install python3-venv  then re-run this script."))
        sys.exit(1)

# ── Virtual environment ───────────────────────────────────────────────────────

def setup_venv(venv_dir: Path, sys_info: dict) -> Path:
    if sys_info["is_linux"]:
        ensure_linux_venv_package()

    if venv_dir.exists():
        print(f"  {green('✓')} Virtual env already exists at {dim(str(venv_dir))}")
    else:
        print(f"  Creating virtual env at {dim(str(venv_dir))} …")
        run([sys.executable, "-m", "venv", str(venv_dir)])
        print(f"  {green('✓')} Virtual env created.")

    # Resolve the python binary inside the venv
    if sys_info["is_windows"]:
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    if not venv_python.exists():
        print(red(f"  Could not find {venv_python}. Venv creation may have failed."))
        sys.exit(1)

    return venv_python

# ── Dependencies ──────────────────────────────────────────────────────────────

def install_deps(venv_python: Path, req_file: Path):
    print(f"  Installing dependencies from {dim(str(req_file))} …")
    pip_install(venv_python, "pip", "wheel")
    run([str(venv_python), "-m", "pip", "install", "--quiet", "-r", str(req_file)])
    print(f"  {green('✓')} Dependencies installed.")

# ── .env configuration ────────────────────────────────────────────────────────

def configure_env(env_path: Path, example_path: Path):
    print()
    hr()
    print(f"  {bold('Configure .env')}")
    print()

    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip().strip('"').strip("'")

    def current(key: str, fallback: str = "") -> str:
        return existing.get(key, fallback)

    print(f"  {dim('Required fields:')}")
    api_key  = ask("OpenRouter API key", default=current("OPENROUTER_API_KEY"), secret=True) \
               or ask("OpenRouter API key (cannot be empty)", secret=True)
    username = ask("Login username", default=current("APP_AUTH_USERNAME", "admin"))
    password = ask("Login password", default=current("APP_AUTH_PASSWORD"), secret=True) \
               or ask("Login password (cannot be empty)", secret=True)

    print()
    print(f"  {dim('Optional fields (press Enter to keep defaults):')}")
    max_attempts  = ask("Max failed login attempts before lockout", default=current("AUTH_MAX_ATTEMPTS", "5"))
    lockout_secs  = ask("Lockout duration (seconds)",               default=current("AUTH_LOCKOUT_SECONDS", "300"))
    trusted_days  = ask("Trusted device session (days)",            default=current("TRUSTED_SESSION_DAYS", "30"))
    session_hours = ask("Non-trusted session (hours)",              default=current("SESSION_HOURS", "12"))
    cookie_secure = ask("Cookie secure (true if HTTPS)",            default=current("COOKIE_SECURE", "false"))

    lines = [
        f"OPENROUTER_API_KEY={api_key}",
        f"APP_AUTH_USERNAME={username}",
        f"APP_AUTH_PASSWORD={password}",
        "# Optional:",
        f"AUTH_MAX_ATTEMPTS={max_attempts}",
        f"AUTH_LOCKOUT_SECONDS={lockout_secs}",
        f"TRUSTED_SESSION_DAYS={trusted_days}",
        f"SESSION_HOURS={session_hours}",
        f"COOKIE_SECURE={cookie_secure}",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    print(f"  {green('✓')} .env written to {dim(str(env_path))}")

# ── user.md ───────────────────────────────────────────────────────────────────

def configure_user_md(user_md_path: Path):
    print()
    hr()
    print(f"  {bold('User profile (user.md)')}")
    print(f"  {dim('This is the persistent identity/persona the AI will remember.')}")
    print(f"  {dim('You can edit it anytime from the app UI.')}")
    print()

    if user_md_path.exists():
        print(f"  {green('✓')} user.md already exists — skipping (edit it in the UI).")
        return

    name  = ask("Your name or persona name", default="User")
    notes = ask("Brief description (role, preferences, tone — or leave blank)", default="")

    content = f"# User\n\n**Name:** {name}\n"
    if notes:
        content += f"\n{notes}\n"
    content += "\nWho you are notes for role-play identity, background, tone, and boundaries.\n"

    user_md_path.write_text(content, encoding="utf-8")
    print(f"  {green('✓')} user.md created.")

# ── Usage hint ────────────────────────────────────────────────────────────────

def print_usage(venv_dir: Path, sys_info: dict, install_dir: Path):
    print()
    hr("═")
    print()
    print(bold("  ✅  Setup complete! Here's how to use Virtual Life:"))
    print()

    if sys_info["is_windows"]:
        activate = f"{venv_dir}\\Scripts\\activate"
    else:
        activate = f"source {venv_dir}/bin/activate"

    print(f"  {bold('1.')} Activate the virtual environment:")
    print(f"     {cyan(activate)}")
    print()
    print(f"  {bold('2.')} Start the app:")
    print(f"     {cyan('python main.py')}")
    print()
    print(f"  {bold('3.')} Open in your browser:")
    print(f"     {cyan('http://127.0.0.1:7860')}")
    print()
    print(f"  {dim('Tip: re-run this script anytime to update your .env settings.')}")
    print()
    hr("═")
    print()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print_banner()

    script_dir  = Path(__file__).parent.resolve()
    install_dir = ensure_repo(script_dir)
    os.chdir(install_dir)

    print(f"  {bold('Directory')}: {dim(str(install_dir))}")
    print()

    sys_info = detect_system()

    # ── Virtual env location ──────────────────────────────────────────────────
    hr()
    print(f"  {bold('Virtual environment')}")
    print()
    venv_name = ask("Virtual env folder name", default=".venv")
    venv_dir  = install_dir / venv_name
    print()

    venv_python = setup_venv(venv_dir, sys_info)

    # ── Dependencies ──────────────────────────────────────────────────────────
    req_file = install_dir / "requirements.txt"
    if req_file.exists():
        install_deps(venv_python, req_file)
    else:
        print(yellow("  requirements.txt not found — skipping dependency install."))

    # ── .env ──────────────────────────────────────────────────────────────────
    configure_env(install_dir / ".env", install_dir / ".env.example")

    # ── user.md ───────────────────────────────────────────────────────────────
    configure_user_md(install_dir / "user.md")

    # ── Done ──────────────────────────────────────────────────────────────────
    print_usage(venv_dir, sys_info, install_dir)


if __name__ == "__main__":
    main()
