from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "static" / "acs-calling.js"
SEARCH = "allowAccessRawMediaStream: false, deviceSelectionTimeoutInMs"
REPLACE = "allowAccessRawMediaStream: true, deviceSelectionTimeoutInMs"


def main() -> None:
    if not TARGET.exists():
        raise SystemExit(f"{TARGET} does not exist. Run build_acs.py first.")
    text = TARGET.read_text(encoding="utf-8")
    if REPLACE in text:
        print("ACS bundle already patched")
        return
    if SEARCH not in text:
        raise SystemExit("Patch marker not found. ACS SDK may have changed.")
    TARGET.write_text(text.replace(SEARCH, REPLACE, 1), encoding="utf-8")
    print("ACS bundle patched for remoteAudioStream.getMediaStream()")


if __name__ == "__main__":
    main()
