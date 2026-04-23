import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Discord webhook for website→Discord announcements
# Set in .env as: DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
