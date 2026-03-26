"""CLI entry point for the UoA Event Agent."""

import argparse
import logging
import sys

from src.pipeline import ALL_SOURCES, run_pipeline
from src.promo_pipeline import ALL_PROMO_SOURCES, run_promo_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="UoA Event Agent — 抓取奥克兰活动信息 & 超市特价",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.cli --output output/events.json
  python -m src.cli --sources eventfinda --no-ai --verbose
  python -m src.cli --sources eventfinda eventbrite --limit 20
  python -m src.cli --deals
  python -m src.cli --deals --sources woolworths --limit 50
  python -m src.cli --deals --notion
        """,
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=None,
        help=(
            f"Data sources to scrape. "
            f"Events: {', '.join(ALL_SOURCES)}. "
            f"Promos (--deals): {', '.join(ALL_PROMO_SOURCES)}. "
            f"Default: all for the selected mode."
        ),
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
    parser.add_argument(
        "--generate-content",
        action="store_true",
        help="Generate Chinese push content for S/A priority events",
    )
    parser.add_argument(
        "--notion",
        action="store_true",
        help="Push scored events to Notion database",
    )
    parser.add_argument(
        "--weekly-roundup",
        nargs="?",
        const="full",
        default=None,
        choices=["full", "supplement"],
        help="Generate and push weekly roundup to Notion (default: full, or 'supplement' for Thu-Sun)",
    )
    parser.add_argument(
        "--notion-cleanup",
        action="store_true",
        help="Clean up Notion: deduplicate pages and refresh content with latest prompt",
    )
    parser.add_argument(
        "--fix-expired",
        action="store_true",
        help="One-time fix: rename legacy '已过期' pages to '收集未发'",
    )
    parser.add_argument(
        "--deals",
        action="store_true",
        help="Scrape supermarket promotional campaigns instead of events",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Telegram notifications for new promos (requires --deals)",
    )
    parser.add_argument(
        "--screenshot",
        action="store_true",
        help="Take screenshots of supermarket specials pages (requires --deals)",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # One-time fix for legacy expired pages
    if args.fix_expired:
        from src.notion_sync import NotionSync
        try:
            syncer = NotionSync()
            print("🔧 正在修复历史「已过期」页面为「收集未发」...")
            fixed = syncer.fix_legacy_expired_to_uncollected()
            print(f"  修复完成：更新了 {fixed} 个页面")
        except Exception as e:
            logging.error(f"Fix expired failed: {e}", exc_info=True)
            sys.exit(1)
        return

    # Handle Notion cleanup separately (no scraping needed)
    if args.notion_cleanup:
        from src.notion_sync import NotionSync
        try:
            syncer = NotionSync()
            print("🔍 正在去重 Notion 页面...")
            archived = syncer.deduplicate_pages()
            print(f"  去重完成：归档了 {archived} 个重复页面")
            print("📝 正在用新 prompt 刷新文案...")
            refreshed = syncer.refresh_content()
            print(f"  刷新完成：更新了 {refreshed} 个页面的文案")
            print("\n✅ Notion 清理完成！")
        except Exception as e:
            logging.error(f"Notion cleanup failed: {e}", exc_info=True)
            sys.exit(1)
        return

    # --- Supermarket promos pipeline ---
    if args.deals:
        promo_sources = args.sources
        if promo_sources:
            invalid = [s for s in promo_sources if s not in ALL_PROMO_SOURCES]
            if invalid:
                parser.error(
                    f"Invalid promo sources: {invalid}. "
                    f"Choose from: {', '.join(ALL_PROMO_SOURCES)}"
                )

        # Screenshot-only mode
        if args.screenshot:
            try:
                from src.screenshot import take_all_screenshots
                paths = take_all_screenshots(stores=promo_sources)
                print(f"\n✅ Screenshots saved: {paths}")
            except Exception as e:
                logging.error(f"Screenshot failed: {e}", exc_info=True)
                sys.exit(1)
            return

        try:
            output_path = run_promo_pipeline(
                sources=promo_sources,
                output_path=args.output,
                push_notion=args.notion,
                send_notifications=args.notify,
            )
            print(f"\n✅ Done! Promos output: {output_path}")
        except KeyboardInterrupt:
            print("\nAborted.")
            sys.exit(1)
        except Exception as e:
            logging.error(f"Promo pipeline failed: {e}", exc_info=True)
            sys.exit(1)
        return

    # --- Events pipeline ---
    # Validate sources against event sources
    if args.sources:
        invalid = [s for s in args.sources if s not in ALL_SOURCES]
        if invalid:
            parser.error(
                f"Invalid event sources: {invalid}. "
                f"Choose from: {', '.join(ALL_SOURCES)}"
            )
    try:
        output_path = run_pipeline(
            sources=args.sources,
            use_ai=not args.no_ai,
            limit=args.limit,
            output_path=args.output,
            verbose=args.verbose,
            generate_content=args.generate_content,
            push_notion=args.notion,
            weekly_roundup=args.weekly_roundup,
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
