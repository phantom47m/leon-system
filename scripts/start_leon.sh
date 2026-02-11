#!/bin/bash
# Quick-start Leon

LEON_DIR="$HOME/leon-system"
cd "$LEON_DIR"
source venv/bin/activate

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  ANTHROPIC_API_KEY not set"
    echo "   export ANTHROPIC_API_KEY=sk-..."
    echo "   Or Leon will use ~/.anthropic/api_key"
    echo
fi

# Default to CLI mode, pass --gui for GUI
MODE="${1:---cli}"

echo "🤖 Starting Leon ($MODE)..."
python3 main.py "$MODE"
