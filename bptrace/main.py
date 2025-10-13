"""Entry point for branch prediction trace analysis tool."""

import argparse
import sqlite3
import sys

from modules.process import export
from modules.stats import stat


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the trace analysis tool."""
    parser = argparse.ArgumentParser(description="XiangShan branch prediction trace analysis tool")

    parser.add_argument(
        "dbfile",
        help="Input database file path"
    )

    parser.add_argument(
        "-o", "--output",
        default="./trace.csv",
        help="Output CSV file path"
    )

    parser.add_argument(
        "-s", "--start",
        type=int,
        default=0,
        help="Start processing from this clock cycle (minimum STAMP)"
    )

    parser.add_argument(
        "-e", "--end",
        type=int,
        help="Stop processing at this clock cycle (maximum STAMP)"
    )

    parser.add_argument(
        "-n", "--num",
        type=int,
        help="Number of branch entries to process (cannot be used with -e)"
    )

    parser.add_argument(
        "--only-addr",
        type=lambda x: int(x, base=0),
        help="Only output entries with specified startVAddr"
    )

    parser.add_argument(
        "--only-mispredict",
        action="store_true",
        help="Only output mispredicted entries"
    )

    parser.add_argument(
        "--only-override",
        action="store_true",
        help="Only output entries with s1/s3 prediction override"
    )

    parser.add_argument(
        "--brtype",
        action="store_true",
        help="Include brType field in the output"
    )

    parser.add_argument(
        "--rasaction",
        action="store_true",
        help="Include rasAction field in the output"
    )

    parser.add_argument(
        "--target",
        action="store_true",
        help="Include target field in the output"
    )

    parser.add_argument(
        "-m", "--meta",
        help="Comma-separated list of metadata fields to include in output (e.g., XXX for META_XXX field)"
    )

    parser.add_argument(
        "--render-prunedaddr",
        action="store_true",
        help="Render real address of PrunedAddr"
    )

    # Statistics options
    parser.add_argument(
        "--only-stats",
        action="store_true",
        help="Only generate statistics, do not export CSV"
    )

    parser.add_argument(
        "--stats-mispredict",
        type=int,
        default=10,
        help="Show top N blocks (startVAddr) with most mispredictions"
    )

    parser.add_argument(
        "--stats-br-mispredict",
        type=int,
        default=20,
        help="Show top N branches (startVAddr, position) with most mispredictions"
    )

    parser.add_argument(
        "--stats-type-mispredict",
        type=bool,
        default=True,
        help="Show misprediction counts by attribute (brType, rasAction)"
    )

    args = parser.parse_args()

    if args.end is not None and args.num is not None:
        print("Cannot use -e and -n at the same time", file=sys.stderr)
        sys.exit(1)

    return args

def main() -> None:
    """Entry point."""
    args = parse_args()

    conn = sqlite3.connect(args.dbfile)
    cur = conn.cursor()

    stat(args, cur)

    if not args.only_stats:
        export(args, cur)

    conn.close()

if __name__ == "__main__":
    main()
