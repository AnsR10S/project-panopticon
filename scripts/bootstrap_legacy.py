\"\"\"Legacy bootstrap helper: inspects local environment and checks prerequisite tools.\"\"\"

import os
import platform
import shutil
import sys


def check_python_version() -> bool:
    print(f"Detected Python {sys.version}")
    if sys.version_info < (3, 11):
        print("ERROR: Python 3.11 or newer is required.")
        return False
    return True


def check_system_tools() -> list[str]:
    missing = []
    for tool in ["git", "node", "npm"]:
        path = shutil.which(tool)
        if path:
            print(f"Found {tool}: {path}")
        else:
            print(f"Warning: {tool} not found on PATH.")
            missing.append(tool)
    return missing


def check_virtualenv() -> bool:
    in_venv = sys.prefix != sys.base_prefix or "VIRTUAL_ENV" in os.environ
    if in_venv:
        print("Running inside an active virtual environment.")
    else:
        print("Notice: No active virtual environment detected.")
    return in_venv


def main() -> None:
    print("=== Auditor's Paradox Legacy Bootstrap ===")
    print(f"Platform: {platform.platform()}")
    ok = check_python_version()
    missing = check_system_tools()
    check_virtualenv()
    if not ok:
        sys.exit(1)
    if missing:
        print(f"Bootstrap finished with warnings for: {', '.join(missing)}")
    else:
        print("Bootstrap check passed cleanly.")


if __name__ == "__main__":
    main()
