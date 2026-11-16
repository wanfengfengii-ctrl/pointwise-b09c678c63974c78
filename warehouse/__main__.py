import argparse
import json
import tempfile
from pathlib import Path
from . import Warehouse
from .samples import seed


def demo():
    with tempfile.TemporaryDirectory(prefix="warehouse-demo-") as directory:
        path = str(Path(directory) / "archive.sqlite")
        with Warehouse(path) as warehouse:
            ids = seed(warehouse)
            historical = warehouse.get_result("completed")
        with Warehouse(path) as warehouse:
            pending = warehouse.run_job("pending")
            warehouse.create_job("mixed", ["history", "recent"], ids["reader"], "demo.mixed")
            mixed = warehouse.run_job("mixed")
            print(json.dumps({
                "schema_ids": ids,
                "historical": historical,
                "reopened_pending": pending,
                "mixed": mixed,
                "new_reader": warehouse.get_result("new-reader"),
                "failed": warehouse.get_result("failed")
            }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Schema-bound warehouse archive")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="build, process and reopen a finite temporary archive")
    build = sub.add_parser("seed", help="create a complete SDK1 sample archive")
    build.add_argument("--database", required=True)
    args = parser.parse_args()
    if args.command == "demo":
        demo()
    else:
        with Warehouse(args.database) as warehouse:
            print(json.dumps(seed(warehouse), sort_keys=True))


if __name__ == "__main__":
    main()
