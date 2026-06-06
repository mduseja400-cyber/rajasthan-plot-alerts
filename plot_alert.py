import os
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

URL = "https://udhonline.rajasthan.gov.in/Portal/ViewAuction"

def send_telegram(message):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message}
    )

response = requests.get(URL, timeout=30)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

title = soup.title.text.strip() if soup.title else "UDH Auction Page"

message = f"""🔔 Rajasthan Auction Check

Portal Status: Accessible ✅
Page Title: {title}

System checked UDH successfully.
"""

send_telegram(message)
print(message)
