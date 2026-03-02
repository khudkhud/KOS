"""Application entrypoint wrappers for RobotOS runtime."""

from __future__ import annotations

import argparse
import json

from robotos.runtime.bootstrap import BuildOptions, EmbodimentProfile, build, build_http_app, build_system
from robotos.runtime.demo_wiring import run_demo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cancel", action="store_true", help="cancel session mid run")
    parser.add_argument("--preempt", action="store_true", help="simulate preempt + resume")
    parser.add_argument("--target-gone", action="store_true", help="simulate family says target already left")
    parser.add_argument("--build", action="store_true", help="output runtime build assessment report")
    args = parser.parse_args()
    if args.build:
        print(json.dumps(build(), ensure_ascii=False, indent=2))
        return
    result = run_demo(cancel_midway=args.cancel, do_preempt=args.preempt, emit_target_gone=args.target_gone)
    print(json.dumps(result, ensure_ascii=False, indent=2))


__all__ = [
    "BuildOptions",
    "EmbodimentProfile",
    "build_system",
    "build",
    "build_http_app",
    "run_demo",
    "main",
]


if __name__ == "__main__":
    main()
