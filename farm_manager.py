"""
Farm manager — runs the print cycle in a background thread.

Cycle for each print job:
  1. Pre-sweep  : send clearing G-code (ensures plate is empty before printing)
  2. Upload + start print
  3. Monitor until FINISH / FAILED
  4. Post-sweep : send clearing G-code (cools 45 min, then pushes print off plate)
  5. Repeat for next job in queue
"""

import io
import re
import time
import threading
import traceback
import zipfile
from typing import Optional

import bambulabs_api as bl


# States that mean "printer is no longer busy"
_IDLE_STATES = ("IDLE", "FINISH", "FAILED", "PAUSE")
# States that mean a print has ended (success or failure)
_DONE_STATES  = ("FINISH", "FAILED", "ERROR", "IDLE")


def _parse_state(raw) -> str:
    """Normalise a GcodeState / PrintStatus object to an uppercase string."""
    return str(raw).upper().replace("GCODESTATE.", "").replace("PRINTSTATUS.", "").strip()


def _safe_filename(name: str) -> str:
    """Replace URL-unsafe characters (spaces, +, etc.) with underscores."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _validate_3mf(data: bytes, plate: int) -> tuple[bool, str]:
    """Check that a 3mf file contains the expected gcode for the given plate."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        gcode_files = [n for n in names if n.endswith(".gcode") and n.startswith("Metadata/plate_")]
        expected = f"Metadata/plate_{plate}.gcode"
        if expected in names:
            return True, f"OK — found {expected} (all gcode files: {gcode_files})"
        elif gcode_files:
            return False, f"plate_{plate}.gcode NOT found. Available: {gcode_files}"
        else:
            return False, f"No gcode files found in 3mf. Contents: {names[:20]}"
    except Exception as exc:
        return False, f"Could not read 3mf zip: {exc}"


def _gcode_lines(gcode_text: str) -> list[str]:
    """Strip comments and blank lines from a G-code block, return command list."""
    lines = []
    for raw in gcode_text.splitlines():
        line = raw.split(";")[0].strip()
        if line:
            lines.append(line)
    return lines


class FarmManager:
    def __init__(self):
        self.running: bool = False
        self.current_step: str = ""
        self.log: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self, printer: bl.Printer, queue: list[dict], clearing_gcode: str):
        """Hand off the queue to the background thread and start the farm cycle."""
        if self.running:
            return
        with self._lock:
            self.running = True
            self.log = []
            self.current_step = "Starting…"
        self._thread = threading.Thread(
            target=self._run_farm,
            args=(printer, list(queue), clearing_gcode),
            daemon=True,
            name="BambuFarm",
        )
        self._thread.start()

    def stop(self):
        """Signal the farm thread to stop after the current operation."""
        self.running = False
        self.current_step = "Stopping…"

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        with self._lock:
            self.log.append(entry)
        print(entry)  # mirror to console for easy debugging

    def _send_gcode(self, printer: bl.Printer, gcode_text: str) -> bool:
        """Send G-code line by line. Returns True if all commands were sent."""
        commands = _gcode_lines(gcode_text)
        if not commands:
            self._log("WARNING: No G-code commands found (only comments/blanks?).")
            return False
        self._log(f"Sending {len(commands)} G-code command(s)…")
        for cmd in commands:
            if not self.running:
                return False
            try:
                printer.gcode(cmd, gcode_check=False)
                time.sleep(0.1)
            except Exception as exc:
                self._log(f"WARNING: G-code '{cmd}' raised: {exc}")
        return True

    def _wait_for_idle(self, printer: bl.Printer, timeout: int = 7200) -> bool:
        """
        Poll printer state until it reaches an idle-like state.
        timeout: seconds to wait (default 2 h to cover 45-min cooling dwell).
        Returns True if idle was reached, False on timeout or stop signal.
        """
        deadline = time.time() + timeout
        last_state = ""
        while time.time() < deadline:
            if not self.running:
                return False
            try:
                state = _parse_state(printer.get_state())
                if state != last_state:
                    self._log(f"Printer state → {state}")
                    last_state = state
                if any(s in state for s in _IDLE_STATES):
                    return True
            except Exception as exc:
                self._log(f"WARNING: Could not read printer state: {exc}")
            time.sleep(15)
        self._log("TIMEOUT waiting for printer to become idle.")
        return False

    def _wait_for_print_start(self, printer: bl.Printer, timeout: int = 120) -> bool:
        """
        Wait until the printer leaves idle and enters an active printing state.
        Returns True if printing started, False on timeout or stop signal.
        """
        _BUSY_STATES = ("RUNNING", "PREPARE", "SLICING", "PRINTING")
        deadline = time.time() + timeout
        self._log("Waiting for printer to begin printing…")
        while time.time() < deadline:
            if not self.running:
                return False
            try:
                state = _parse_state(printer.get_state())
                self._log(f"  poll: state={state}")
                if any(s in state for s in _BUSY_STATES):
                    self._log(f"Printer entered busy state: {state}")
                    return True
            except Exception as exc:
                self._log(f"WARNING: Could not read state: {exc}")
            time.sleep(5)
        self._log("TIMEOUT: printer never entered a busy state — job may have been rejected.")
        return False

    def _wait_for_print_complete(self, printer: bl.Printer) -> str:
        """Poll until the active print finishes. Returns the final state string."""
        last_pct: Optional[int] = None
        while self.running:
            try:
                state = _parse_state(printer.get_state())
                pct   = printer.get_percentage()
                if pct != last_pct and pct is not None:
                    self._log(f"Progress: {pct}%")
                    last_pct = pct
                if any(s in state for s in _DONE_STATES):
                    return state
            except Exception as exc:
                self._log(f"WARNING: Status check failed: {exc}")
            time.sleep(30)
        return "STOPPED"

    def _clearing_sequence(self, printer: bl.Printer, gcode_text: str, label: str):
        """Send clearing G-code and wait for the printer to go idle."""
        self._log(f"── {label} ──")
        self.current_step = label
        sent = self._send_gcode(printer, gcode_text)
        if not sent:
            self._log("Skipping wait — no G-code was sent.")
            return
        # Small pause so the printer registers the new commands before we poll
        time.sleep(5)
        self._wait_for_idle(printer, timeout=7200)  # 2-hour ceiling
        self._log(f"{label} complete.")

    # ── Main farm loop ─────────────────────────────────────────────────────────

    def _run_farm(self, printer: bl.Printer, queue: list[dict], clearing_gcode: str):
        total = len(queue)
        try:
            self._log(f"Farm started — {total} job(s) queued.")

            # ── Pre-sweep: make sure the plate is empty before job 1 ──────────
            if not self.running:
                return
            self._clearing_sequence(printer, clearing_gcode, "Pre-sweep (ensuring clean plate)")

            job_num = 0
            while queue and self.running:
                job_num += 1
                job      = queue.pop(0)
                filename = _safe_filename(job["filename"])
                plate    = job.get("plate", 1)
                data     = job["bytes"]
                remaining = len(queue)

                self._log(
                    f"Job {job_num}/{total}: '{filename}' (plate {plate})"
                    f" — {remaining} job(s) remaining after this"
                )
                self.current_step = f"Uploading: {filename}"

                # ── Validate 3mf contents ────────────────────────────────────
                if filename.endswith(".3mf"):
                    valid, detail = _validate_3mf(data, plate)
                    self._log(f"3mf validation: {detail}")
                    if not valid:
                        self._log(f"ERROR: Invalid 3mf — skipping job.")
                        continue

                # ── Upload ────────────────────────────────────────────────────
                try:
                    self._log(f"Uploading {filename}…")
                    result = printer.upload_file(io.BytesIO(data), filename)
                    self._log(f"FTP result: {result!r}")
                    if result is not None and "226" not in str(result):
                        self._log(f"ERROR: FTP upload failed (no 226 in response) — skipping job.")
                        continue
                    time.sleep(3)
                    self._log("Upload complete.")

                    # List FTP root to confirm file is there
                    try:
                        ftp_result, ftp_files = printer.ftp_client.list_directory()
                        self._log(f"FTP directory listing: {ftp_files}")
                    except Exception as ftp_exc:
                        self._log(f"WARNING: Could not list FTP directory: {ftp_exc}")

                except Exception as exc:
                    self._log(f"ERROR uploading '{filename}': {exc} — skipping job.")
                    continue

                if not self.running:
                    break

                # ── Start print ───────────────────────────────────────────────
                try:
                    self._log(f"Starting print: '{filename}', plate {plate}…")
                    self._log(f"MQTT payload → url='ftp:///{filename}', param='Metadata/plate_{plate}.gcode'")
                    print_ok = printer.start_print(filename, plate)
                    self._log(f"start_print returned: {print_ok}")
                except Exception as exc:
                    self._log(f"ERROR starting print: {exc} — skipping job.")
                    continue

                # Brief pause so the printer registers the new job
                time.sleep(5)

                # ── Wait for print to actually start ──────────────────────────
                self.current_step = f"Starting ({job_num}/{total}): {filename}"
                started = self._wait_for_print_start(printer, timeout=120)
                if not started:
                    self._log(f"Job '{filename}' never started — skipping post-sweep.")
                    continue

                # ── Monitor ───────────────────────────────────────────────────
                self.current_step = f"Printing ({job_num}/{total}): {filename}"
                final_state = self._wait_for_print_complete(printer)

                if "FINISH" in final_state:
                    self._log(f"Print finished: '{filename}'")
                elif "STOPPED" in final_state:
                    self._log("Farm stopped by user during print.")
                    break
                else:
                    self._log(f"Print ended with state '{final_state}': '{filename}'")

                if not self.running:
                    break

                # ── Post-sweep: cool + push completed print off plate ─────────
                self._clearing_sequence(
                    printer, clearing_gcode,
                    f"Post-sweep (clearing print {job_num}/{total})"
                )

            # ── Done ──────────────────────────────────────────────────────────
            if self.running:
                self._log(f"All {job_num} job(s) complete! Farm finished.")
            else:
                self._log("Farm stopped by user.")

        except Exception as exc:
            self._log(f"FATAL farm error: {exc}")
            self._log(traceback.format_exc())
        finally:
            self.running = False
            self.current_step = ""
