"""CLI utilities for RobotOS (currently OSM replay)."""

from __future__ import annotations

import argparse
import json

from robotos.kernel.osm.store import OSMStore


def cmd_replay(path: str) -> None:
    store = OSMStore()
    store.replay_from_file(path)
    print(json.dumps({"version": store.version, "events": [e.__dict__ for e in store.event_log]}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="RobotOS utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)
    replay = sub.add_parser("replay", help="Replay OSM event log JSONL")
    replay.add_argument("--path", required=True)
    args = parser.parse_args()

    if args.cmd == "replay":
        cmd_replay(args.path)


if __name__ == "__main__":
    main()
