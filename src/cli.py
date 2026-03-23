"""CLI entry point for the UoA Event Agent."""

import argparse
import logging
import sys

from src.pipeline import ALL_SOURCES, run_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="UoA Event Agent — 抓取奥克兰活动信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.cli --output output/events.json
  python -m src.cli --sources eventfinda --no-ai --verbose
  python -m src.cli --sources eventfinda eventbrite --limit 20
        """,
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=ALL_SOURCES,
        default=None,
        help=f"Data sources to scrape (default: all). Choices: {', '.join(ALL_SOURCES)}",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI scoring (rule-based only)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max events per source (default: 50)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        output_path = run_pipeline(
            sources=args.sources,
            use_ai=not args.no_ai,
            limit=args.limit,
            output_path=args.output,
            verbose=args.verbose,
        )
        print(f"\n✅ Done! Output: {output_path}")
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
