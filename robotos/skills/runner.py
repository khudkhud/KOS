from __future__ import annotations

import argparse

from robotos.kernel.action.dds import run_skill_server_forever


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a RobotOS skill action server process")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--duration-ms", type=int, default=1000)
    parser.add_argument("--backend", default="cyclonedds")
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    run_skill_server_forever(tool=args.tool, duration_ms=args.duration_ms, backend=args.backend, fail=args.fail)


if __name__ == "__main__":
    main()
