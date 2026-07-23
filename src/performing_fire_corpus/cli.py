from __future__ import annotations

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="performing-fire-corpus",
        description="Privacy-safe, rights-aware corpus tooling.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
