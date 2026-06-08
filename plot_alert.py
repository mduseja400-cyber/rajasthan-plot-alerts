import os
import re
import sys
import time
import base64
import requests
from bs4 import BeautifulSoup

BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPO")  # "mduseja400-cyber/rajasthan-plot-alerts"

BASE_URL  = "https://udhonline.rajasthan.gov.in"
LIST_URL  = BASE_URL + "/Portal/AuctionListNew"
API_URL   = BASE_URL + "/Portal/GetLiveAuctionDetailedReport"
SEEN_PATH = "seen_auctions.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}


# ── GitHub se seen_auctions.txt read/write ────────────────
def load_seen_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{SEEN_PATH}"
    r = requests.get(url, headers={"Authorization": f"token {GITHUB_TOKEN}"}, timeout=15)
    if r.status_code == 200:
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        seen = set(content.strip().splitlines())
        print(f"Loaded {len(seen)} seen IDs from GitHub")
        return seen, data["sha"]
    elif r.status_code == 404:
        print("seen_auctions.txt not found on GitHub, starting fresh")
        return set(), None
    else:
        print(f"GitHub load error: {r.status_code}")
        return set(), None


def save_seen_github(seen, sha):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{SEEN_PATH}"
    content = "\n".join(sorted(seen)) + "\n"
    encoded = base64.b64encode(content.encode()).decode()
    payload = {
        "message": "Update seen auctions [skip ci]",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, json=payload,
                     headers={"Authorization": f"token {GITHUB_TOKEN}"},
                     timeout=15)
    if r.status_code in (200, 201):
        print(f"Saved {len(seen)} IDs to GitHub")
    else:
        print(f"GitHub save error: {r.status_code} — {r.text[:100]}")


# ── Telegram ──────────────────────────────────────────────
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


# ── Fetch ─────────────────────────────────────────────────
def fetch_page(url, session, post_data=None):
    try:
        if post_data:
            r = session.post(url, data=post_data, headers=HEADERS, timeout=30)
        else:
            r = session.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        if len(r.text) > 200:
            return r.text
    except Exception as e:
        print(f"  Fetch error: {e}")
    return None


# ── Parse ─────────────────────────────────────────────────
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


# ── Main ──────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("JDA Auction Alert Bot — Render")
    print("=" * 55)

    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set"); sys.exit(1)
    if not CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID not set"); sys.exit(1)
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set"); sys.exit(1)
    if not GITHUB_REPO:
        print("ERROR: GITHUB_REPO not set"); sys.exit(1)

    seen, sha = load_seen_github()
    session   = requests.Session()

    # Page 1
    print(f"\nFetching: {LIST_URL}")
    html1 = fetch_page(LIST_URL, session)

    if not html1:
        send_telegram(
            "⚠️ <b>JDA Alert Bot Error</b>\n\n"
            "Website fetch fail ho gayi.\n"
            "Manual check: udhonline.rajasthan.gov.in"
        )
        return

    all_auctions = parse_auctions(html1)
    print(f"Page 1: {len(all_auctions)} auctions")

    # Pagination params
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

    page_links = soup.find_all("a", href=lambda h: h and "GetLiveAuctionDetailedReport" in h)
    max_page = 1
    for lnk in page_links:
        m = re.search(r"page=(\d+)", lnk.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))
    print(f"Total pages: {max_page}")

    # Pages 2..N
    for page in range(2, max_page + 1):
        time.sleep(1.5)
        post_data = {
            "page": str(page), "Paging": "True",
            "pageSize": page_size or "30", "TabViewType": "0",
            "UnitId": unit_id, "SchemeId": scheme_id,
            "UsageType": "0", "IsCorner": "0", "Flag": "",
        }
        html_p = fetch_page(API_URL, session, post_data)
        if html_p:
            pa = parse_auctions(html_p)
            all_auctions.extend(pa)
            print(f"Page {page}: {len(pa)} auctions")
        else:
            print(f"Page {page}: failed")

    print(f"\nTotal: {len(all_auctions)} auctions")

    # Alerts
    new_count = 0
    for a in all_auctions:
        if a["id"] in seen:
            continue
        print(f"  NEW: {a['id']} | {a['title'][:50]}")
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

    save_seen_github(seen, sha)
    print(f"\n✅ Done. New: {new_count} | Total tracked: {len(seen)}")


if __name__ == "__main__":
    main()
