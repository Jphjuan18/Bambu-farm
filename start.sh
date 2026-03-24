#!/bin/bash
# Bambu Farm — double-click this file to start the app
cd "$(dirname "$0")"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install Python dependencies if needed
pip install -r requirements.txt --quiet

# Open the app
streamlit run app.py
