#!/bin/bash
# Install system dependencies for Leon's voice system
# Run with: sudo bash scripts/install-voice-deps.sh

set -e

echo "Installing system packages for Leon voice..."
apt install -y portaudio19-dev libasound2-dev python3.12-dev xdotool scrot xclip

echo ""
echo "Installing Python packages that need compilation..."
pip install --break-system-packages pyaudio

echo ""
echo "All voice dependencies installed!"
echo ""
echo "Next steps:"
echo "  1. Sign up at https://deepgram.com and get an API key"
echo "  2. Sign up at https://elevenlabs.io and get an API key"
echo "  3. Store keys in Leon's vault:"
echo "     /setkey DEEPGRAM_API_KEY <your-key>"
echo "     /setkey ELEVENLABS_API_KEY <your-key>"
echo "  4. Start Leon with voice: python3 main.py --full"
