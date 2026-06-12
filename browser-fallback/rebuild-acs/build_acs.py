from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
OUT = STATIC / "acs-calling.js"

ENTRY = """export * from '@azure/communication-calling';\nexport * from '@azure/communication-common';\n"""


def run(command: list[str], cwd: Path) -> None:
    print(" ".join(command))
    completed = subprocess.run(command, cwd=str(cwd), shell=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.42.1")
    args = parser.parse_args()
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if npm is None or npx is None:
        raise SystemExit("npm is required to build static/acs-calling.js")
    tmp = Path(tempfile.mkdtemp(prefix="lisa-acs-build-"))
    try:
        (tmp / "package.json").write_text(json.dumps({
            "private": True,
            "type": "module",
            "dependencies": {
                "@azure/communication-calling": args.version,
                "@azure/communication-common": "latest",
                "esbuild": "latest",
            },
        }), encoding="utf-8")
        (tmp / "acs-entry.js").write_text(ENTRY, encoding="utf-8")
        run([npm, "install"], tmp)
        run([
            npx, "esbuild", "acs-entry.js",
            "--bundle", "--format=iife",
            "--global-name=AzureCommunicationCalling",
            "--platform=browser", f"--outfile={OUT.as_posix()}",
        ], tmp)
        run([sys.executable, str(Path(__file__).with_name("patch_acs.py"))], ROOT)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"Built {OUT}")


if __name__ == "__main__":
    main()
