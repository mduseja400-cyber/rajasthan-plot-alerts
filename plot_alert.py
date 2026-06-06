import os
import re
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

URL = "https://udhonline.rajasthan.gov.in/Portal/AuctionListNew"


def send_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": message
        }
    )


# Load seen auction IDs
seen = set()
if os.path.exists("seen_auctions.txt"):
    with open("seen_auctions.txt", "r", encoding="utf-8") as f:
        seen = set(line.strip() for line in f if line.strip())

# Download page
response = requests.get(URL, timeout=60)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

new_ids = []

# Find all Jaipur auction rows
for li in soup.find_all("li"):
    text = li.get_text(" ", strip=True)

    if "Id :" in text:
        match = re.search(r"Id\s*:\s*(\d+)", text)

        if match:
            auction_id = match.group(1)

            if auction_id not in seen:
                row_text = li.find_parent("tr").get_text("\n", strip=True)

                message = (
                    "🏠 NEW JDA AUCTION ALERT\n\n"
                    f"Auction ID: {auction_id}\n\n"
                    f"{row_text[:300]}\n\n"
                    f"Check JDA portal for details."
                )

                send_telegram(message)

                seen.add(auction_id)
                new_ids.append(auction_id)

# Save updated IDs
with open("seen_auctions.txt", "w", encoding="utf-8") as f:
    for item in sorted(seen):
        f.write(item + "\n")
 print("Total LI tags:", len(soup.find_all("li")))

for li in soup.find_all("li")[:20]:
    print("DEBUG:", repr(li.get_text(" ", strip=True)))       

print(f"New auctions found: {len(new_ids)}")
