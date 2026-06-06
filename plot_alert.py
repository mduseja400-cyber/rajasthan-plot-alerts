import os
import re
import sys
import time
import subprocess
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
SEEN_FILE = "seen_auctions.txt"
BASE_URL  = "https://udhonline.rajasthan.gov.in"
LIST_URL  = BASE_URL + "/Portal/AuctionListNew"
API_URL   = BASE_URL + "/Portal/GetLiveAuctionDetailedReport"


# ── Telegram ─────────────────────────────────────────────
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


# ── seen_auctions ────────────────────────────────────────
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            seen = {l.strip() for l in f if l.strip()}
    else:
        seen = set()
    print(f"Loaded {len(seen)} seen IDs")
    return seen


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for item in sorted(seen):
            f.write(item + "\n")
    print(f"Saved {len(seen)} IDs")


# ── Fetch via curl (bypasses Python requests fingerprint) ─
def fetch_url_curl(url, post_data=None):
    """
    Use system curl — different TLS fingerprint than Python requests,
    more likely to pass WAF checks.
    """
    cmd = [
        "curl", "-s", "--max-time", "30",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-IN,en;q=0.9,hi;q=0.8",
        "-H", f"Referer: {LIST_URL}",
        "-H", "Connection: keep-alive",
        "--compressed",
        "-L",  # follow redirects
    ]
    if post_data:
        cmd += ["--data-urlencode", "dummy=1"]  # force POST
        for k, v in post_data.items():
            cmd += ["-d", f"{k}={v}"]
    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if result.returncode == 0 and len(result.stdout) > 200:
            return result.stdout
    except Exception as e:
        print(f"  curl error: {e}")
    return None


def fetch_url_requests(url, session, post_data=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Referer": LIST_URL,
    }
    try:
        if post_data:
            r = session.post(url, data=post_data, headers=headers, timeout=30)
        else:
            r = session.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        if len(r.text) > 200:
            return r.text
    except Exception as e:
        print(f"  requests error: {e}")
    return None


def fetch_page(url, session, post_data=None):
    """Try requests first, fall back to curl."""
    html = fetch_url_requests(url, session, post_data)
    if html:
        return html
    print("  requests failed, trying curl...")
    return fetch_url_curl(url, post_data)


# ── Parse auctions from HTML ──────────────────────────────
def parse_auctions(html):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    main_table = max(tables, key=lambda t: len(t.find_all("tr")))
    rows = main_table.find_all("tr")

    auctions = []
    for i, row in enumerate(rows):
        row_text = row.get_text(" ", strip=True)
        id_match = re.search(r"Id\s*:\s*(\d+)", row_text)
        if not id_match:
            continue

        auction_id = id_match.group(1)
        full = row_text
        if i + 1 < len(rows):
            full += " " + rows[i + 1].get_text(" ", strip=True)

        def ex(pattern):
            m = re.search(pattern, full)
            return m.group(1).strip() if m else ""

        auctions.append({
            "id":      auction_id,
            "title":   ex(r"Title\s*:\s*(.+?)(?:Scheme Name\s*:|$)"),
            "scheme":  ex(r"Scheme Name\s*:\s*(.+?)(?:Property Number\s*:|$)"),
            "prop_no": ex(r"Property Number\s*:\s*(.+?)(?:Property Area\s*:|$)"),
            "area":    ex(r"Property Area\s*:\s*(.+?)(?:Usage Type\s*:|$)"),
            "usage":   ex(r"Usage Type\s*:\s*(.+?)(?:EMD|$)"),
            "emd_end": ex(r"EMD Deposit End Date\s*:\s*(.+?)(?:Auction|Last|Bid|$)")[:25],
            "bsp":     ex(r"(?:Bid Start Price|BSP).*?:\s*([\d,]+\.?\d*)"),
        })

    return auctions


# ── Main ─────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("JDA Auction Alert Bot")
    print("=" * 55)

    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set"); sys.exit(1)
    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not set"); sys.exit(1)

    seen    = load_seen()
    session = requests.Session()

    # ── Step 1: Fetch page 1 (main listing page) ──
    print(f"\nFetching page 1: {LIST_URL}")
    html1 = fetch_page(LIST_URL, session)

    if not html1:
        print("FATAL: Could not fetch page 1")
        send_telegram(
            "⚠️ <b>JDA Alert Bot Error</b>\n\n"
            "Website fetch fail ho gayi.\n"
            "Possible reasons:\n"
            "• Website temporarily down\n"
            "• IP block by server\n\n"
            "Manual check: udhonline.rajasthan.gov.in"
        )
        return

    # Save debug
    with open("debug_page1.html", "w", encoding="utf-8") as f:
        f.write(html1)

    all_auctions = parse_auctions(html1)
    print(f"  Page 1: {len(all_auctions)} auctions found")

    # ── Step 2: Extract form params for pagination ──
    soup      = BeautifulSoup(html1, "html.parser")
    form      = soup.find("form", id="SearchForm") or soup.find("form")
    scheme_id = unit_id = page_size = ""

    if form:
        def fval(name):
            inp = form.find("input", {"name": name})
            return inp.get("value", "") if inp else ""
        scheme_id = fval("SchemeId")
        unit_id   = fval("UnitId")
        page_size = fval("PageSize") or "30"

    print(f"  SchemeId={scheme_id} UnitId={unit_id} PageSize={page_size}")

    # Find max page
    page_links = soup.find_all("a", href=lambda h: h and "GetLiveAuctionDetailedReport" in h)
    max_page = 1
    for lnk in page_links:
        m = re.search(r"page=(\d+)", lnk.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))
    print(f"  Total pages: {max_page}")

    # ── Step 3: Fetch pages 2..N ──
    for page in range(2, max_page + 1):
        time.sleep(1.5)
        post_data = {
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
        print(f"\nFetching page {page}...")
        html_p = fetch_page(API_URL, session, post_data)
        if html_p:
            page_auctions = parse_auctions(html_p)
            all_auctions.extend(page_auctions)
            print(f"  Page {page}: {len(page_auctions)} auctions")
        else:
            print(f"  Page {page}: fetch failed, skipping")

    print(f"\nTotal auctions fetched: {len(all_auctions)}")

    # ── Step 4: Send alerts for new auctions ──
    new_count = 0
    for a in all_auctions:
        if a["id"] in seen:
            continue

        print(f"  NEW: ID={a['id']} | {a['title'][:50]}")
        msg = (
            f"🏠 <b>NEW JDA AUCTION ALERT</b>\n\n"
            f"<b>ID:</b> {a['id']}\n"
            f"<b>Title:</b> {a['title']}\n"
            f"<b>Scheme:</b> {a['scheme']}\n"
            f"<b>Property No:</b> {a['prop_no']}\n"
            f"<b>Area:</b> {a['area']}\n"
            f"<b>Usage:</b> {a['usage']}\n"
            f"<b>BSP:</b> ₹{a['bsp']}\n"
            f"<b>EMD End:</b> {a['emd_end']}\n\n"
            f"🔗 <a href='{LIST_URL}'>Portal Link</a>"
        )
        send_telegram(msg)
        seen.add(a["id"])
        new_count += 1
        time.sleep(1)

    save_seen(seen)
    print(f"\n✅ Done. New: {new_count} | Total tracked: {len(seen)}")


if __name__ == "__main__":
    main()
