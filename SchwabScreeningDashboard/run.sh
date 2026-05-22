#!/usr/bin/env bash
# run.sh – Quick-start script for the Schwab Covered Call Dashboard

set -e

# Create venv if it doesn't exist
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install / upgrade dependencies
pip install -q -r requirements.txt

# Check for .env
if [ ! -f ".env" ]; then
  echo ""
  echo "ERROR: .env file not found."
  echo "Run:  cp .env.example .env  and fill in your Schwab credentials."
  exit 1
fi

echo ""
echo "Starting Schwab Covered Call Dashboard..."
echo "Open http://127.0.0.1:5000 in your browser."
echo "Press Ctrl+C to stop."
echo ""

python app.py
