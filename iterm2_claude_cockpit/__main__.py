"""Tiny CLI for managing extensions.

Usage:
  python -m iterm2_claude_cockpit ext list
  python -m iterm2_claude_cockpit ext enable <name>
  python -m iterm2_claude_cockpit ext disable <name>

Edits iterm2_claude_cockpit/extensions.json. Restart iTerm2 to pick up changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the bundled package importable when run via `python -m iterm2_claude_cockpit`
# from outside the package directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extensions import _loader as ext_loader  # noqa: E402


def _cmd_list() -> int:
    available = ext_loader.list_available()
    enabled = set(ext_loader.list_enabled())
    if not available:
        print("(no extensions found under iterm2_claude_cockpit/extensions/)")
        return 0
    width = max(len(n) for n in available)
    for name in available:
        mark = "✓" if name in enabled else " "
        state = "enabled" if name in enabled else "disabled"
        print(f"  {mark} {name.ljust(width)}  {state}")
    return 0


def _cmd_enable(name: str) -> int:
    available = ext_loader.list_available()
    if name not in available:
        print(f"error: no such extension {name!r}", file=sys.stderr)
        if available:
            print(f"available: {', '.join(available)}", file=sys.stderr)
        return 1
    config = ext_loader.read_config()
    enabled = list(config.get("enabled") or [])
    if name in enabled:
        print(f"{name} is already enabled.")
        return 0
    enabled.append(name)
    config["enabled"] = enabled
    ext_loader.write_config(config)
    print(f"enabled {name}. Restart iTerm2 to apply.")
    return 0


def _cmd_disable(name: str) -> int:
    config = ext_loader.read_config()
    enabled = list(config.get("enabled") or [])
    if name not in enabled:
        print(f"{name} is not enabled.")
        return 0
    enabled.remove(name)
    config["enabled"] = enabled
    ext_loader.write_config(config)
    print(f"disabled {name}. Restart iTerm2 to apply.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m iterm2_claude_cockpit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ext = sub.add_parser("ext", help="manage extensions")
    ext_sub = ext.add_subparsers(dest="ext_cmd", required=True)
    ext_sub.add_parser("list", help="list extensions and their state")
    p_enable = ext_sub.add_parser("enable", help="enable an extension")
    p_enable.add_argument("name")
    p_disable = ext_sub.add_parser("disable", help="disable an extension")
    p_disable.add_argument("name")

    args = parser.parse_args(argv)
    if args.cmd == "ext":
        if args.ext_cmd == "list":
            return _cmd_list()
        if args.ext_cmd == "enable":
            return _cmd_enable(args.name)
        if args.ext_cmd == "disable":
            return _cmd_disable(args.name)
    parser.error("unreachable")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
