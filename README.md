# Bambu Farm

A Streamlit-based management tool for Bambu Lab 3D printer farms.

## Prerequisites

- **Python 3** (with `pip3` available)

## Getting Started

1. **Clone or unzip** the project
2. **Open a terminal** and navigate to the project folder

### macOS / Linux

3. Make the start script executable (only needed once):
   ```bash
   chmod +x start.sh
   ```
4. Run the app:
   ```bash
   ./start.sh
   ```

### Windows

3. Double-click **`start.bat`**, or run it from a terminal:
   ```cmd
   start.bat
   ```

Both scripts will automatically create a virtual environment, install dependencies (`streamlit`, `bambulabs-api`), and launch the app.

## Troubleshooting

- **`python: command not found`** — Python 3 isn't installed or not on your PATH
- **`streamlit: command not found`** — Try running `python -m streamlit run app.py` instead
