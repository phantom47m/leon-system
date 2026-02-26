#!/bin/bash
# One-time setup: log into your accounts in Leon's automation browser.
# Sessions are saved permanently — run this once, never again.

OC="$HOME/.openclaw/bin/openclaw"
PROFILE="openclaw"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Leon Browser Account Setup                        ║"
echo "║   Log into your accounts — sessions save forever.  ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Start browser
echo "Starting browser..."
$OC browser start --browser-profile "$PROFILE"
sleep 3

# Open account login pages
echo "Opening account pages..."
for URL in \
    "https://github.com/login" \
    "https://accounts.google.com" \
    "https://railway.app/login" \
    "https://discord.com/login" \
    "https://www.reddit.com/login"
do
    $OC browser open "$URL" --browser-profile "$PROFILE"
    sleep 1
done

echo ""
echo "A Brave browser window should now be open on your screen."
echo "Log into each tab. Take your time — the browser stays open."
echo ""
echo "Press ENTER when you're done logging in to all accounts."
read -r

echo "Done! Your sessions are saved to:"
echo "  ~/.openclaw/browser/$PROFILE/user-data/"
echo ""
echo "Leon will use these sessions for all future browser tasks."
