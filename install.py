#!/usr/bin/env python3
"""Unified Python installer for Amil orchestrator.

Installs amil-utils (Python CLI) and copies orchestrator files
(agents, commands, workflows, hooks) to the Claude Code config directory.

Usage:
    python install.py [--global | --local] [--uninstall]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
SOURCE_ROOT = Path(__file__).resolve().parent
PIPELINE_PYTHON = SOURCE_ROOT / "python"

# Directories to install
INSTALL_DIRS = {
    "agents": "agents",
    "amil": "amil",
    "commands": "commands",
    "hooks": "hooks",
}

# ── Helpers ──────────────────────────────────────────────────────────────────

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}[OK]{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}[ERROR]{RESET} {msg}")


# ── Install ──────────────────────────────────────────────────────────────────


def install_pip_package() -> bool:
    """Install amil-utils Python package."""
    if not PIPELINE_PYTHON.exists():
        warn(f"Pipeline Python directory not found at {PIPELINE_PYTHON}")
        warn("  amil-utils must be installed separately: pip install amil-utils")
        return False

    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(PIPELINE_PYTHON), "--quiet"],
            check=True,
            capture_output=True,
            text=True,
        )
        ok("Installed amil-utils Python package")
        return True
    except subprocess.CalledProcessError as e:
        # Retry with --break-system-packages if the error is about externally-managed environment
        if "externally-managed-environment" in (e.stderr or ""):
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", str(PIPELINE_PYTHON),
                     "--break-system-packages", "--quiet"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                ok("Installed amil-utils Python package (with --break-system-packages)")
                return True
            except subprocess.CalledProcessError:
                pass
        warn(f"Could not install amil-utils: {e.stderr.strip()}")
        warn("  Install manually: pip install -e python")
        return False


def copy_dir(src: Path, dest: Path) -> int:
    """Copy directory recursively, return file count."""
    if not src.exists():
        return 0
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return sum(1 for _ in dest.rglob("*") if _.is_file())


def install_global() -> None:
    """Install orchestrator files to Claude Code config directory."""
    target = CLAUDE_CONFIG_DIR
    if not target.exists():
        fail(f"Claude Code config directory not found at {target}")
        print(f"  Install Claude Code first or set CLAUDE_CONFIG_DIR")
        sys.exit(1)

    print(f"\n{BOLD}Installing Amil to {target}...{RESET}\n")

    # Install pip package
    install_pip_package()

    # Copy directories
    for src_rel, dest_rel in INSTALL_DIRS.items():
        src = SOURCE_ROOT / src_rel
        dest = target / dest_rel
        if src.exists():
            count = copy_dir(src, dest)
            ok(f"Installed {dest_rel}/ ({count} files)")

    # Verify amil-utils is on PATH
    if shutil.which("amil-utils"):
        ok("amil-utils CLI available on PATH")
    else:
        warn("amil-utils not on PATH — you may need to add ~/.local/bin to PATH")

    print(f"\n{GREEN}{BOLD}Installation complete.{RESET}")
    print(f"  {DIM}Get started: /amil:new-project{RESET}\n")


def install_local() -> None:
    """Install orchestrator files to current directory."""
    target = Path.cwd()
    print(f"\n{BOLD}Installing Amil locally to {target}...{RESET}\n")

    install_pip_package()

    src = SOURCE_ROOT / "amil"
    if src.exists():
        dest = target / "amil"
        count = copy_dir(src, dest)
        ok(f"Installed amil/ ({count} files)")

    print(f"\n{GREEN}{BOLD}Installation complete.{RESET}\n")


def uninstall() -> None:
    """Remove orchestrator files from Claude Code config directory."""
    target = CLAUDE_CONFIG_DIR
    print(f"\n{BOLD}Uninstalling Amil from {target}...{RESET}\n")

    for dest_rel in INSTALL_DIRS.values():
        dest = target / dest_rel
        if dest.exists():
            if dest_rel == "agents":
                # Only remove amil-* agent files
                for f in dest.glob("amil-*"):
                    f.unlink()
                    ok(f"Removed {f.name}")
            elif dest_rel == "hooks":
                # Only remove amil-* hook files
                for f in dest.glob("amil-*"):
                    f.unlink()
                    ok(f"Removed {f.name}")
            else:
                shutil.rmtree(dest)
                ok(f"Removed {dest_rel}/")

    print(f"\n{GREEN}Amil uninstalled.{RESET}\n")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Amil orchestrator installer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-g", "--global", dest="global_install", action="store_true",
                       help="Install globally (to ~/.claude/)")
    group.add_argument("-l", "--local", action="store_true",
                       help="Install locally (to current directory)")
    group.add_argument("-u", "--uninstall", action="store_true",
                       help="Uninstall Amil from Claude Code")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
    elif args.local:
        install_local()
    else:
        install_global()


if __name__ == "__main__":
    main()
