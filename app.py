"""
Bambu Farm — Streamlit UI
=========================
Pages:
  Dashboard   — connect to printers, view live status, test light
  Farm Mode   — upload files, manage print queue, run the farm cycle
  Configure   — set printer credentials and clearing G-code
"""

import json
import time
from pathlib import Path
from typing import Optional

import streamlit as st
import bambulabs_api as bl

from farm_manager import FarmManager

# ── Config helpers ─────────────────────────────────────────────────────────────

CONFIG_FILE = Path("config.json")


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"printers": [], "clearing_gcode": ""}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Session state ──────────────────────────────────────────────────────────────

def _init():
    if "config" not in st.session_state:
        st.session_state.config = load_config()
    if "connections" not in st.session_state:
        # { printer_id: bl.Printer }
        st.session_state.connections: dict[int, bl.Printer] = {}
    if "farm" not in st.session_state:
        st.session_state.farm = FarmManager()
    if "queue" not in st.session_state:
        # list of { filename, plate, bytes }
        st.session_state.queue: list[dict] = []


# ── Connection helpers ─────────────────────────────────────────────────────────

def _is_connected(pid: int) -> bool:
    p = st.session_state.connections.get(pid)
    if p is None:
        return False
    try:
        return bool(p.mqtt_client_connected())
    except Exception:
        return False


def _get_printer(pid: int) -> Optional[bl.Printer]:
    return st.session_state.connections.get(pid)


def _connect(cfg: dict) -> tuple[bool, str]:
    pid = cfg["id"]
    try:
        p = bl.Printer(cfg["ip"], cfg["access_code"], cfg["serial"])
        p.mqtt_start()
        # Wait for MQTT handshake
        for _ in range(6):
            time.sleep(1)
            if p.mqtt_client_connected():
                break
        if not p.mqtt_client_connected():
            return False, "MQTT connection did not establish — check IP, serial and access code."
        st.session_state.connections[pid] = p
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


def _disconnect(pid: int):
    p = st.session_state.connections.pop(pid, None)
    if p:
        try:
            p.mqtt_stop()
        except Exception:
            pass


# ── Dashboard page ─────────────────────────────────────────────────────────────

def page_dashboard():
    st.header("Dashboard")

    printers = st.session_state.config.get("printers", [])
    if not printers:
        st.info("No printers configured yet — go to **Configure** to add one.")
        return

    _, col_auto = st.columns([5, 1])
    auto_refresh = col_auto.checkbox("Auto-refresh (5 s)")

    for cfg in printers:
        pid = cfg["id"]
        connected = _is_connected(pid)
        p = _get_printer(pid)

        with st.container(border=True):
            c_title, c_btn, c_light = st.columns([4, 1, 1])

            with c_title:
                badge = "🟢 Connected" if connected else "⚫ Disconnected"
                st.subheader(f"{cfg['name']}  —  {badge}")
                st.caption(f"IP: `{cfg['ip']}`  |  Serial: `{cfg['serial']}`")

            with c_btn:
                if connected:
                    if st.button("Disconnect", key=f"disc_{pid}"):
                        _disconnect(pid)
                        st.rerun()
                else:
                    if st.button("Connect", key=f"conn_{pid}", type="primary"):
                        with st.spinner(f"Connecting to {cfg['name']}…"):
                            ok, msg = _connect(cfg)
                        if ok:
                            st.rerun()
                        else:
                            st.error(f"Connection failed: {msg}")

            with c_light:
                if connected and p:
                    if st.button("💡 Test Light", key=f"light_{pid}"):
                        with st.spinner("Toggling light…"):
                            try:
                                p.turn_light_on()
                                time.sleep(2)
                                p.turn_light_off()
                                st.toast(f"{cfg['name']}: light test OK!")
                            except Exception as exc:
                                st.error(str(exc))

            # Live metrics
            if connected and p:
                try:
                    state_raw = str(p.get_state())
                    state = (
                        state_raw
                        .replace("GcodeState.", "")
                        .replace("PrintStatus.", "")
                    )
                    pct       = p.get_percentage() or 0
                    bed_t     = p.get_bed_temperature() or 0
                    nozzle_t  = p.get_nozzle_temperature() or 0
                    layer     = p.current_layer_num()
                    total_lay = p.total_layer_num()
                    remain_s  = p.get_time() or 0
                    fname     = p.get_file_name() or "—"

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("State",    state)
                    m2.metric("Progress", f"{pct}%")
                    m3.metric("Bed",      f"{bed_t}°C")
                    m4.metric("Nozzle",   f"{nozzle_t}°C")

                    if pct > 0:
                        st.progress(int(min(pct, 100)) / 100)

                    parts = []
                    if fname != "—":
                        parts.append(f"File: **{fname}**")
                    if layer and total_lay:
                        parts.append(f"Layer {layer} / {total_lay}")
                    if remain_s > 0:
                        parts.append(f"{remain_s // 60} min remaining")
                    if parts:
                        st.caption("  |  ".join(parts))

                except Exception as exc:
                    st.caption(f"Status unavailable: {exc}")

    if auto_refresh:
        time.sleep(5)
        st.rerun()


# ── Farm Mode page ─────────────────────────────────────────────────────────────

def page_farm_mode():
    st.header("Farm Mode")

    config  = st.session_state.config
    farm    = st.session_state.farm
    printers = config.get("printers", [])

    if not printers:
        st.info("No printers configured — go to **Configure** first.")
        return

    connected_printers = [
        cfg for cfg in printers if _is_connected(cfg["id"])
    ]
    if not connected_printers:
        st.warning("No printers connected — go to **Dashboard** and connect a printer.")
        return

    # Printer selector
    printer_map = {cfg["name"]: cfg for cfg in connected_printers}
    selected_name = st.selectbox("Active Printer", list(printer_map.keys()))
    selected_cfg  = printer_map[selected_name]
    selected_p    = _get_printer(selected_cfg["id"])

    st.divider()

    # ── Queue management ──────────────────────────────────────────────────────
    left, right = st.columns(2)

    with left:
        st.subheader("Add to Queue")
        uploaded = st.file_uploader(
            "Print file (.3mf or .gcode)",
            type=["3mf", "gcode"],
            label_visibility="collapsed",
            disabled=farm.running,
        )
        plate = st.number_input(
            "Plate number",
            min_value=1, max_value=16, value=1,
            help="Which plate inside the .3mf to print (usually 1).",
            disabled=farm.running,
        )
        if st.button("Add to Queue", disabled=(uploaded is None or farm.running)):
            st.session_state.queue.append({
                "filename": uploaded.name,
                "plate":    int(plate),
                "bytes":    uploaded.read(),
            })
            st.toast(f"Added: {uploaded.name}")
            st.rerun()

    with right:
        q = st.session_state.queue
        st.subheader(f"Print Queue  ({len(q)} job{'s' if len(q) != 1 else ''})")
        if not q:
            st.caption("Empty — upload a file to get started.")
        else:
            for i, job in enumerate(q):
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"**{i + 1}.** {job['filename']}  *(plate {job['plate']})*")
                if not farm.running:
                    if c2.button("✕", key=f"qrm_{i}"):
                        q.pop(i)
                        st.rerun()

    st.divider()

    # ── Farm controls ─────────────────────────────────────────────────────────
    st.subheader("Farm Control")

    clearing_gcode = config.get("clearing_gcode", "").strip()
    if not clearing_gcode:
        st.warning(
            "No clearing G-code configured. "
            "Add it in **Configure** before starting the farm."
        )

    c_start, c_stop = st.columns(2)

    with c_start:
        can_start = (
            not farm.running
            and len(st.session_state.queue) > 0
            and bool(clearing_gcode)
            and selected_p is not None
        )
        if st.button(
            "▶  Start Farm",
            type="primary",
            use_container_width=True,
            disabled=not can_start,
        ):
            farm.start(selected_p, st.session_state.queue, clearing_gcode)
            st.session_state.queue = []  # queue handed off to farm thread
            st.rerun()

    with c_stop:
        if st.button(
            "■  Stop Farm",
            use_container_width=True,
            disabled=not farm.running,
        ):
            farm.stop()
            st.rerun()

    st.divider()

    # ── Status & log ──────────────────────────────────────────────────────────
    st.subheader("Status")
    if farm.running:
        st.success(f"🔄  {farm.current_step}")
    else:
        st.info("Idle")

    if farm.log:
        st.subheader("Log")
        # Newest entries at the top
        log_text = "\n".join(reversed(farm.log[-60:]))
        st.code(log_text, language=None)

    # Auto-refresh while farm is running
    if farm.running:
        time.sleep(3)
        st.rerun()


# ── Configure page ─────────────────────────────────────────────────────────────

def page_configure():
    st.header("Configure")

    config   = st.session_state.config
    printers = config.setdefault("printers", [])

    # ── Printers ──────────────────────────────────────────────────────────────
    st.subheader("Printers")

    if len(printers) < 6:
        if st.button("+ Add Printer"):
            new_id = max((p["id"] for p in printers), default=0) + 1
            printers.append({
                "id":          new_id,
                "name":        f"Printer {new_id}",
                "ip":          "",
                "serial":      "",
                "access_code": "",
            })
            save_config(config)
            st.rerun()
    else:
        st.caption("Maximum of 6 printers reached.")

    delete_id = None
    dirty = False

    for cfg in printers:
        pid = cfg["id"]
        with st.expander(
            f"**{cfg['name']}**  —  {cfg['ip'] or 'no IP set'}",
            expanded=True,
        ):
            col_header, col_del = st.columns([5, 1])
            with col_del:
                if st.button("Remove", key=f"rm_{pid}"):
                    delete_id = pid

            n  = st.text_input("Name",          value=cfg["name"],        key=f"n_{pid}")
            ip = st.text_input("IP Address",    value=cfg["ip"],          key=f"ip_{pid}",
                               placeholder="192.168.1.100")
            sn = st.text_input("Serial Number", value=cfg["serial"],      key=f"sn_{pid}",
                               placeholder="e.g. AC12309BH109")
            ac = st.text_input("Access Code",   value=cfg["access_code"], key=f"ac_{pid}",
                               type="password", placeholder="8-digit code from printer screen")

            if n != cfg["name"] or ip != cfg["ip"] or sn != cfg["serial"] or ac != cfg["access_code"]:
                cfg["name"]        = n
                cfg["ip"]          = ip
                cfg["serial"]      = sn
                cfg["access_code"] = ac
                dirty = True

    if delete_id is not None:
        config["printers"] = [p for p in printers if p["id"] != delete_id]
        _disconnect(delete_id)
        save_config(config)
        st.rerun()

    st.divider()

    # ── Clearing G-Code ───────────────────────────────────────────────────────
    st.subheader("Clearing G-Code")
    st.caption(
        "Runs **before** each print (pre-sweep, to ensure the plate is empty) "
        "and **after** each print (post-sweep, to cool and push the finished print off). "
        "Lines starting with `;` are treated as comments and are ignored."
    )

    new_gcode = st.text_area(
        "clearing_gcode",
        value=config.get("clearing_gcode", ""),
        height=240,
        label_visibility="collapsed",
        placeholder=(
            "; Paste your clearing G-code here\n"
            ";\n"
            "; Typical structure for an angled-plate farm:\n"
            ";   G28              ; home all axes\n"
            ";   G4 P2700000      ; wait 45 min (45×60×1000 ms) for plate to cool\n"
            ";   G1 Y300 F2000    ; sweep print off the angled plate\n"
            ";   G28              ; re-home before next print\n"
        ),
    )

    if new_gcode != config.get("clearing_gcode", ""):
        config["clearing_gcode"] = new_gcode
        dirty = True

    col_save, col_hint = st.columns([1, 3])
    with col_save:
        if st.button("💾  Save Configuration", type="primary"):
            save_config(config)
            st.session_state.config = config
            st.success("Configuration saved!")
            dirty = False
    with col_hint:
        if dirty:
            st.caption("⚠️ Unsaved changes — press Save to persist.")


# ── Sidebar + routing ──────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Bambu Farm",
        page_icon="🖨️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init()

    with st.sidebar:
        st.title("🖨️ Bambu Farm")
        st.divider()

        page = st.radio(
            "Navigation",
            ["Dashboard", "Farm Mode", "Configure"],
            label_visibility="collapsed",
        )

        st.divider()

        # Mini status panel
        config  = st.session_state.config
        farm    = st.session_state.farm
        printers = config.get("printers", [])

        if printers:
            st.caption("**Printers**")
            for cfg in printers:
                icon = "🟢" if _is_connected(cfg["id"]) else "⚫"
                st.caption(f"{icon}  {cfg['name']}")

        queue_len = len(st.session_state.queue)
        if queue_len:
            st.caption(f"**Queue:** {queue_len} job{'s' if queue_len != 1 else ''}")

        if farm.running:
            st.divider()
            st.caption(f"🔄 **Farm running**")
            st.caption(farm.current_step)

    if page == "Dashboard":
        page_dashboard()
    elif page == "Farm Mode":
        page_farm_mode()
    elif page == "Configure":
        page_configure()


if __name__ == "__main__":
    main()
