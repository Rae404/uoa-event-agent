"""Fetch event cover images for 3/26 events."""
import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
OUT_DIR = "output/covers_0326"

EVENTS = [
    ("01_Harry_Potter_Cocktail", "https://www.eventbrite.co.nz/e/harry-potter-cocktail-class-acs-tickets-1985026037313"),
    ("02_Taste_of_Onehunga", "https://ourauckland.aucklandcouncil.govt.nz/events/2026/03/taste-of-onehunga/"),
    ("03_Strings_Workshops", "https://www.eventbrite.co.nz/e/strings-performance-workshops-tickets-1984657677539"),
    ("04_Songcraft_Workshops", "https://www.eventbrite.co.nz/e/songcraft-music-production-workshops-tickets-1984657404723"),
    ("05_Peace_Week_Empathy", "https://www.eventbrite.co.nz/e/peace-week-weaving-empathy-workshop-tickets-1985247072435"),
    ("06_Alpha_Domus_Wine", "https://www.eventbrite.co.nz/e/alpha-domus-wine-night-in-point-chev-tickets-1984654764827"),
    ("07_Navigate_Fortify", "https://www.eventbrite.com/e/navigate-fortify-prevent-tickets-1982939502431"),
    ("08_Asian_Women_Lawyers", "https://www.eventbrite.co.nz/e/nz-asian-women-lawyers-committee-speed-networking-night-tickets-1984344500819"),
    ("09_Disrupting_Exploitation", "https://www.eventbrite.co.uk/e/launch-event-disrupting-exploitation-self-assessment-and-guidance-tickets-1982201641469"),
    ("10_Change_Wisdom", "https://www.eventbrite.co.nz/e/change-wisdom-circle-leadership-capacity-under-pressure-tickets-1982941611740"),
    ("11_Click_Networking", "https://www.eventbrite.co.nz/e/the-click-business-networking-evening-tickets-1983030410339"),
    ("12_Footloose_Musical", "https://www.eventbrite.co.nz/e/saint-kentigern-college-senior-school-musical-footloose-tickets-1983320630395"),
]


def extract_og_image(url):
    """Get og:image URL from a page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if not og or not og.get("content"):
            return None
        img_url = og["content"]

        # Eventbrite: extract the cdn.evbuc.com original URL
        if "img.evbuc.com" in img_url or "_next/image" in img_url:
            # Try to find cdn.evbuc.com URL nested inside
            match = re.search(r"(https?://cdn\.evbuc\.com/images/[^?&\"]+)", unquote(unquote(img_url)))
            if match:
                img_url = match.group(1)

        # Make absolute if relative
        if img_url.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(url)
            img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"

        return img_url
    except Exception as e:
        print(f"  Error fetching page: {e}")
        return None


def download_image(img_url, filename):
    """Download image to file."""
    try:
        resp = requests.get(img_url, headers=HEADERS, timeout=20, stream=True)
        resp.raise_for_status()
        # Determine extension from content-type
        ct = resp.headers.get("content-type", "")
        ext = ".jpg"
        if "png" in ct:
            ext = ".png"
        elif "webp" in ct:
            ext = ".webp"
        filepath = os.path.join(OUT_DIR, filename + ext)
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        size_kb = os.path.getsize(filepath) / 1024
        print(f"  ✓ {filepath} ({size_kb:.0f} KB)")
        return True
    except Exception as e:
        print(f"  ✗ Download failed: {e}")
        return False


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    success = 0
    for name, url in EVENTS:
        print(f"\n{name}")
        print(f"  URL: {url}")
        img_url = extract_og_image(url)
        if img_url:
            print(f"  Image: {img_url[:80]}...")
            if download_image(img_url, name):
                success += 1
        else:
            print("  ✗ No og:image found")

    print(f"\n{'='*40}")
    print(f"Downloaded {success}/{len(EVENTS)} covers to {OUT_DIR}/")
