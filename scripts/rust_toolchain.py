"""Read the repository Rust toolchain source of truth.

The exact compiler channel lives only in ``rust-toolchain.toml``.  Cargo's
``rust-version`` remains a derived minimum because Cargo manifests cannot
interpolate another file; architecture fitness verifies that relationship.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_channel(root: str | Path = ROOT) -> str:
    """Return the exact channel declared by ``rust-toolchain.toml``."""

    path = Path(root) / "rust-toolchain.toml"
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    channel = payload.get("toolchain", {}).get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("rust-toolchain.toml must declare a non-empty toolchain.channel")
    return channel.strip()


def derived_minimum(channel: str) -> str:
    """Return Cargo's compatible major/minor minimum for an exact channel."""

    parts = channel.split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        raise ValueError(f"Rust channel must be numeric major.minor[.patch], got {channel!r}")
    return ".".join(parts[:2])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--minimum", action="store_true")
    args = parser.parse_args()
    channel = read_channel(args.root)
    print(derived_minimum(channel) if args.minimum else channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
