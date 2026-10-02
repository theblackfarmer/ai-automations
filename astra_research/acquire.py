"""Acquire byte-identical research inputs from the immutable frozen revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

FREEZE = Path(__file__).resolve().parents[1] / "research/astra6/FREEZE_2026-10-02.json"
BASE_URL = "https://raw.githubusercontent.com/s-k-28/nq-es-trader-5k-payout"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def acquire_one(output: Path, name: str, revision: str, expected_sha256: str) -> dict:
    """Reuse valid files; validate downloaded bytes before atomic publication.

    A corrupt existing input is an error rather than silently replaced evidence.
    No source values, timestamps or row order are ever modified here.
    """
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError("input name must be a plain filename")
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("source revision must be a full lowercase commit SHA")
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("source sha256 is mandatory")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / name
    url = f"{BASE_URL}/{revision}/data/{name}"
    if destination.exists():
        actual = sha256_file(destination)
        if actual != expected_sha256:
            raise ValueError(f"SHA256 mismatch for existing {name}: expected {expected_sha256}, got {actual}")
        reused = True
    else:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", suffix=".partial", dir=output)
        temporary_path = Path(temporary)
        try:
            request = Request(url, headers={"User-Agent": "ASTRA6-frozen-research/1.0"})
            with os.fdopen(descriptor, "wb") as target:
                with urlopen(request, timeout=180) as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
            actual = sha256_file(temporary_path)
            if actual != expected_sha256:
                raise ValueError(f"SHA256 mismatch for downloaded {name}: expected {expected_sha256}, got {actual}")
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        reused = False
    return {"filename": name, "revision": revision, "url": url,
            "sha256": actual, "bytes": destination.stat().st_size, "reused": reused}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data_cache"), help="Verified raw-input directory")
    args = parser.parse_args(argv)
    freeze = json.loads(FREEZE.read_text())
    data = freeze["data"]
    records = [acquire_one(args.output, data[f"{kind}_file"], data["revision"], data[f"{kind}_sha256"])
               for kind in ("historical", "forward")]
    manifest = {"freeze_id": freeze["id"], "freeze_sha256": sha256_file(FREEZE), "inputs": records}
    (args.output / "acquisition_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
