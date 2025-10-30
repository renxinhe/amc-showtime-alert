#!/usr/bin/env python3
"""
Quick test script to verify the scraper works before running full scrape.
Tests scraping a single date from one theater.
"""

from amc_scraper import AMCShowtimeScraper
from datetime import datetime, timedelta


def test_single_date():
    """Test scraping a single date"""
    print("🧪 Testing AMC Scraper...")
    print("=" * 60)

    try:
        # Create scraper
        scraper = AMCShowtimeScraper(config_path="config.json")

        # Test with tomorrow's date
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        theater = scraper.config['theaters'][0]  # First theater in config

        print(f"\n📍 Theater: {theater['name']}")
        print(f"📅 Date: {tomorrow}")
        print("\n⏳ Fetching showtimes...\n")

        # Scrape
        result = scraper.scrape_date(tomorrow, theater)

        # Display results
        print("=" * 60)
        print("RESULTS")
        print("=" * 60)

        if result.success:
            print(f"✅ Success! Found {len(result.movies)} movies\n")

            for i, movie in enumerate(result.movies, 1):
                runtime = f" ({movie.runtime}min)" if movie.runtime else ""
                rating = f" [{movie.rating}]" if movie.rating else ""
                print(f"{i}. {movie.name}{runtime}{rating}")
                print(f"   Showtimes: {', '.join(movie.showtimes)}")
                print()

        else:
            print(f"❌ Failed: {result.error_message}")

        # Show stats
        print("=" * 60)
        print("STATISTICS")
        print("=" * 60)
        print(f"Total requests: {scraper.stats['total_requests']}")
        print(f"Successful: {scraper.stats['successful_requests']}")
        print(f"Failed: {scraper.stats['failed_requests']}")
        print(f"Movies found: {scraper.stats['total_movies_found']}")

        if result.success:
            print("\n✅ Test passed! You can now run: python amc_scraper.py")
        else:
            print("\n⚠️  Test failed. Check the logs in logs/ directory")

    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        print("Make sure config.json exists in the current directory")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_single_date()
