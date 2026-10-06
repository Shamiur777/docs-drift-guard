"""Command line interface: `python -m sample_lib` or `sample-lib`."""
import argparse


def build_parser():
    parser = argparse.ArgumentParser(prog="sample-lib", description="Browse Ledgerly records.")
    parser.add_argument("--limit", type=int, default=50, help="maximum number of records to show")
    parser.add_argument("--status", default="open", help="only show records with this status")
    parser.add_argument("--output", default="table", help="output format: table or json")
    parser.add_argument("--verbose", "-v", action="store_true", help="print extra detail")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen without changing anything")
    parser.add_argument("--config", default="sample.toml", help="path to the settings file")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return 0 if args else 1
