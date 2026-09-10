#!/usr/bin/env python3
"""Fetch benchmark sources pinned to the research checkouts.

Usage: python scripts/prepare_references.py [agentdojo appworld ...]
Install each benchmark's dependencies in a separate Python 3.11 environment.
This fetches source/data repositories, but does not run experiments or download
AppWorld's separately distributed runtime data. Use the pinned AppWorld CLI
to prepare those data under CERTCF_APPWORLD_ROOT (default: ./data/appworld).
Historical phase/validation runners require outputs from their preceding stages;
those outputs and frozen study records are intentionally not shipped here.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

REFERENCES = {
    "agentdojo": ("https://github.com/sequrity-ai/agentdojo.git", "357c80dea9af34323f709c3505d9e6d224654c7e"),
    "appworld": ("https://github.com/StonyBrookNLP/appworld.git", "42b5bcf3cd334fee33f0c37c02070a9f5807add5"),
    "thinkingbox": ("https://github.com/microsoft/thinkingbox.git", "40c1212f9582ca90175079bc313e530e9e9a4981"),
    "thinkingbox-data": ("https://github.com/microsoft/thinkingbox-data.git", "fcaba4c1a9debec42fda7f15bf29fe6d6b46c431"),
    "toolsandbox": ("https://github.com/apple/ToolSandbox.git", "165848b9a78cead7ca7fe7c89c688b58e6501219"),
}


def prepare(name: str, root: Path) -> None:
    url, commit = REFERENCES[name]
    target = root / name
    if target.exists():
        if not (target / ".git").exists():
            raise RuntimeError(f"Refusing to replace non-checkout directory: {target}")
        head = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(target), "status", "--porcelain"], text=True).strip()
        if head != commit or dirty:
            raise RuntimeError(f"Existing checkout differs from the pin or contains changes: {target}")
        print(f"Already prepared: {name} {commit}")
        return
    target.mkdir(parents=True)
    subprocess.run(["git", "init", str(target)], check=True)
    subprocess.run(["git", "-C", str(target), "remote", "add", "origin", url], check=True)
    subprocess.run(["git", "-C", str(target), "fetch", "--depth=1", "origin", commit], check=True)
    subprocess.run(["git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD"], check=True)
    print(f"Prepared: {name} {commit}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="Benchmark names; defaults to all pinned sources")
    args = parser.parse_args()
    names = args.names or list(REFERENCES)
    for name in names:
        if name not in REFERENCES:
            parser.error(f"Unknown benchmark {name!r}; choose from {', '.join(REFERENCES)}")
    root = Path(__file__).resolve().parents[1] / "reference"
    for name in names:
        prepare(name, root)


if __name__ == "__main__":
    main()
