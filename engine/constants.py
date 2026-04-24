MM_USER_ID = 0

# =========================================================
# ADMIN (Market Maker / Oracle) Discord ID
# This is the Quantara bot account — the active MM account.
# =========================================================
ADMIN_ID = 1496179139118633080  # Quantara (bot account)

# =========================================================
# KNOWN USERS — Discord ID → display name
# Add new traders here as they join.
# IDs must be plain integers (no quotes).
# =========================================================
KNOWN_USERS = {
    0:                    "MM (System)",
    1496179139118633080:  "Quantara",      # MM / Admin (active)
    1490706462341726381:  "Mimi",          # MM / Admin (local testing)
    1494347237810114630:  "Basil",         # Trader 1
    1495343512089133117:  "Gogor",         # Trader 2
}

# =========================================================
# DISCORD WEBHOOK URL — #trade-feed channel
# IMPORTANT: keep this private, do not commit to public repos.
# Replace with your new webhook URL after resetting it.
# =========================================================
DISCORD_WEBHOOK_URL = "PASTE_NEW_WEBHOOK_URL_HERE"   # replace this"
