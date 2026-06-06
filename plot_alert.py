import os
import re
import time
import requests
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
SEEN_FILE  = "seen_auctions.txt"
BASE_URL   = "https://udhonline.rajasthan.gov.in"
LIST_URL   = BASE_URL + "/Portal/AuctionListNew"
API_URL    = BASE_URL + "/Portal/GetLiveAuctionDetailedReport"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
    "Referer": BASE_URL + "/Portal/AuctionListNew",
}

# ── Helpers ──────────────────────────────────────────────
def send_telegram(message):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=20,
        )
        print(f"  Telegram: {r.status_code}")
    except Exception as e:
        print(f"  Telegram error: {e}")


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            seen = {line.strip() for line in f if line.strip()}
    else:
        seen = set()
    print(f"Loaded {len(seen)} previously seen IDs")
    return seen


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for item in sorted(seen):
            f.write(item + "\n")
    print(f"Saved {len(seen)} IDs")


# ── Parsing ──────────────────────────────────────────────
def parse_auctions_from_html(html):
    """
    Parse auction rows from table HTML.
    Each auction occupies 2 consecutive <tr> tags:
      TR1: Id, Title, Scheme, Property No, Area, Usage, EMD dates
      TR2: BSP price info
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    main_table = max(tables, key=lambda t: len(t.find_all("tr")))
    rows = main_table.find_all("tr")

    auctions = []
    i = 0
    while i < len(rows):
        row_text = rows[i].get_text(" ", strip=True)
        id_match = re.search(r"Id\s*:\s*(\d+)", row_text)

        if id_match:
            auction_id = id_match.group(1)

            # Combine 2 rows for full details
            full_text = row_text
            if i + 1 < len(rows):
                full_text += " " + rows[i + 1].get_text(" ", strip=True)

            def extract(pattern, text=full_text):
                m = re.search(pattern, text)
                return m.group(1).strip() if m else ""

            auctions.append({
                "id":      auction_id,
                "title":   extract(r"Title\s*:\s*(.+?)(?:Scheme Name\s*:|$)"),
                "scheme":  extract(r"Scheme Name\s*:\s*(.+?)(?:Property Number\s*:|$)"),
                "prop_no": extract(r"Property Number\s*:\s*(.+?)(?:Property Area\s*:|$)"),
                "area":    extract(r"Property Area\s*:\s*(.+?)(?:Usage Type\s*:|$)"),
                "usage":   extract(r"Usage Type\s*:\s*(.+?)(?:EMD|$)"),
                "emd_end": extract(r"EMD Deposit End Date\s*:\s*(.+?)(?:Auction Date|Last|Bid|$)")[:25],
                "bsp":     extract(r"(?:Bid Start Price|BSP).*?:\s*([\d,]+\.?\d*)"),
            })

        i += 1

    return auctions


# ── Fetching ─────────────────────────────────────────────
def get_all_auctions(session):
    """
    Fetch all pages from GetLiveAuctionDetailedReport.
    First load the main page to get SchemeId/UnitId hidden form values,
    then POST to the API for each page.
    """
    print(f"\nFetching main page: {LIST_URL}")
    try:
        resp = session.get(LIST_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  Main page fetch failed: {e}")
        return []

    # Extract hidden form values (SchemeId, UnitId, pageSize)
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", id="SearchForm") or soup.find("form")

    scheme_id  = ""
    unit_id    = ""
    page_size  = "30"

    if form:
        def fval(name):
            inp = form.find("input", {"name": name})
            return inp.get("value", "") if inp else ""
        scheme_id = fval("SchemeId")
        unit_id   = fval("UnitId")
        page_size = fval("PageSize") or "30"

    print(f"  SchemeId={scheme_id} UnitId={unit_id} PageSize={page_size}")

    # Find how many pages exist
    page_links = soup.find_all("a", href=lambda h: h and "GetLiveAuctionDetailedReport" in h)
    max_page = 1
    for link in page_links:
        m = re.search(r"page=(\d+)", link.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))

    print(f"  Total pages to fetch: {max_page}")

    # Parse page 1 from what we already have
    all_auctions = parse_auctions_from_html(resp.text)
    print(f"  Page 1: {len(all_auctions)} auctions")

    # Fetch remaining pages via POST
    post_headers = {
        **HEADERS,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }

    for page in range(2, max_page + 1):
        time.sleep(1)  # Polite delay
        params = {
            "page":        str(page),
            "Paging":      "True",
            "pageSize":    page_size or "30",
            "TabViewType": "0",
            "UnitId":      unit_id,
            "SchemeId":    scheme_id,
            "UsageType":   "0",
            "IsCorner":    "0",
            "Flag":        "",
        }
        try:
            r = session.post(API_URL, data=params, headers=post_headers, timeout=30)
            r.raise_for_status()
            page_auctions = parse_auctions_from_html(r.text)
            all_auctions.extend(page_auctions)
            print(f"  Page {page}: {len(page_auctions)} auctions")
        except Exception as e:
            print(f"  Page {page} error: {e}")

    return all_auctions


# ── Main ─────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("JDA Auction Alert Bot")
    print("=" * 55)

    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set"); return
    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not set"); return

    seen = load_seen()
    session = requests.Session()

    all_auctions = get_all_auctions(session)
    print(f"\nTotal auctions fetched: {len(all_auctions)}")

    if not all_auctions:
        send_telegram(
            "⚠️ <b>JDA Alert Bot Error</b>\n\n"
            "Auctions fetch nahi ho sake.\n"
            "Manual check karein: udhonline.rajasthan.gov.in"
        )
        return

    new_count = 0
    for a in all_auctions:
        if a["id"] in seen:
            continue

        print(f"  NEW: ID={a['id']} | {a['title'][:50]}")

        message = (
            f"🏠 <b>NEW JDA AUCTION ALERT</b>\n\n"
            f"<b>Auction ID:</b> {a['id']}\n"
            f"<b>Title:</b> {a['title']}\n"
            f"<b>Scheme:</b> {a['scheme']}\n"
            f"<b>Property No:</b> {a['prop_no']}\n"
            f"<b>Area:</b> {a['area']}\n"
            f"<b>Usage:</b> {a['usage']}\n"
            f"<b>BSP:</b> ₹{a['bsp']}\n"
            f"<b>EMD End:</b> {a['emd_end']}\n\n"
            f"🔗 <a href='https://udhonline.rajasthan.gov.in/Portal/AuctionListNew'>Portal Link</a>"
        )
        send_telegram(message)
        seen.add(a["id"])
        new_count += 1
        time.sleep(1)

    save_seen(seen)
    print(f"\n✅ Done. New auctions: {new_count} | Total tracked: {len(seen)}")


if __name__ == "__main__":
    main()
