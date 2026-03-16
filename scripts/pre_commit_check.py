#!/usr/bin/env python3
"""Pre-commit checks enforcing CLAUDE.md rules. Run before every commit."""

import subprocess
import sys
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "web")

FAIL = "\033[91mFAIL\033[0m"
OK = "\033[92mOK\033[0m"

errors = []


def check(description, passed):
    if passed:
        print(f"  [{OK}] {description}")
    else:
        print(f"  [{FAIL}] {description}")
        errors.append(description)


def read_file(path):
    with open(path, "r") as f:
        return f.read()


# ── Nav bar checks ──────────────────────────────────────────────────

print("\n=== Checking dashboard.html nav bar ===")
dashboard = read_file(os.path.join(WEB, "dashboard.html"))
check("Has <nav> element", "<nav" in dashboard)
check("Has Instruments Editor link", "instruments.html" in dashboard and "Instruments" in dashboard)
check("Has 'Logged in as' display", "Logged in as" in dashboard)
check("Has Logout button", "logout()" in dashboard.lower() or "Logout" in dashboard)

print("\n=== Checking instruments.html nav bar ===")
instruments = read_file(os.path.join(WEB, "instruments.html"))
check("Has <nav> element", "<nav" in instruments)
check("Has Dashboard link", "dashboard.html" in instruments and "Dashboard" in instruments)
check("Has 'Logged in as' display", "Logged in as" in instruments)
check("Has Logout button", "logout()" in instruments.lower() or "Logout" in instruments)

# ── Hardcoded path check ────────────────────────────────────────────

print("\n=== Checking for hardcoded ~/trading/ paths ===")
hardcoded_files = []
for root, dirs, files in os.walk(REPO):
    # Skip .git, __pycache__, node_modules
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv")]
    for fname in files:
        fpath = os.path.join(root, fname)
        # Only check text files
        if not fname.endswith((".py", ".html", ".js", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh", ".conf")):
            continue
        try:
            content = read_file(fpath)
            if "~/trading" in content or "/root/trading" in content:
                # Allow this script itself and CLAUDE.md
                rel = os.path.relpath(fpath, REPO)
                if rel in ("scripts/pre_commit_check.py", "CLAUDE.md"):
                    continue
                hardcoded_files.append(rel)
        except (UnicodeDecodeError, PermissionError):
            pass

if hardcoded_files:
    for f in hardcoded_files:
        check(f"No hardcoded paths in {f}", False)
else:
    check("No hardcoded ~/trading or /root/trading paths found", True)

# ── Run pytest ──────────────────────────────────────────────────────

print("\n=== Running pytest ===")
test_dir = os.path.join(REPO, "tests")
if os.path.isdir(test_dir):
    result = subprocess.run(
        ["pytest", "tests/", "-v"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    check("All tests pass", result.returncode == 0)
else:
    print("  (no tests/ directory found, skipping)")

# ── Summary ─────────────────────────────────────────────────────────

print("\n" + "=" * 50)
if errors:
    print(f"\033[91m{len(errors)} CHECK(S) FAILED:\033[0m")
    for e in errors:
        print(f"  - {e}")
    print()
    sys.exit(1)
else:
    print("\033[92mAll pre-commit checks passed!\033[0m\n")
    sys.exit(0)
