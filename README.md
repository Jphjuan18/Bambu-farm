# Bambu Farm

A Streamlit-based management tool for Bambu Lab 3D printer farms.

## Prerequisites

- **Python 3** (with `pip3` available)

## Getting Started

1. **Clone or unzip** the project
2. **Open a terminal** and navigate to the project folder
3. Make the start script executable (only needed once):
   ```bash
   chmod +x start.sh
   ```
4. Run the app:
   ```bash
   ./start.sh
   ```

This will automatically create a virtual environment, install dependencies (`streamlit`, `bambulabs-api`), and launch the app.

## Troubleshooting

- **`pip3: command not found`** — Python 3 isn't installed or not on your PATH
- **`streamlit: command not found`** — Try running `python3 -m streamlit run app.py` instead
- **Windows** — `.sh` scripts don't run natively; use WSL, Git Bash, or run the commands manually:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  streamlit run app.py
  ```
