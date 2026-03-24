#!/bin/bash
# Bambu Farm — double-click this file to start the app
cd "$(dirname "$0")"

# Install Python dependencies if needed
pip3 install -r requirements.txt --quiet

# Open the app
streamlit run app.py
