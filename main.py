# Nothing Ear Control — Decky Loader backend (Steam Deck / Linux).
#
# Talks to Nothing / CMF earbuds over Bluetooth Classic RFCOMM, the same protocol as the Windows app
# (learned from the open-source ear-web project: https://github.com/radiance-project/ear-web).
# Not affiliated with Nothing Technology Limited.
#
# Frame: 55 60 01 <cmd lo> <cmd hi> <len> 00 <op id> <payload...> <crc16 lo> <crc16 hi>
# CRC-16: init 0xFFFF, reflected poly 0xA001, over header + payload.
#
# Linux doesn't resolve an RFCOMM service by UUID on connect (Windows does), so the channel is looked up
# with a small SDP client over L2CAP first.

import asyncio
import errno
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import threading
import time

import decky

# ------------------------------------------------------------------ Models

# Google Fast Pair model ID -> (product, model base).
FAST_PAIR = {
    "31D53D": ("Nothing ear (1)", "B181"), "624011": ("Nothing ear (1)", "B181"),
    "1016DD": ("Nothing Ear (stick)", "B157"),
    "ACC520": ("Nothing Ear (2)", "B155"), "DEE8C0": ("Nothing Ear (2)", "B155"),
    "FEB1C7": ("Nothing Ear", "B171"), "A20444": ("Nothing Ear", "B171"),
    "5E3FBC": ("Nothing Ear (a)", "B162"), "03464E": ("Nothing Ear (a)", "B162"),
    "8B6380": ("Nothing Ear (a)", "B162"), "C34F3B": ("Nothing Ear (a)", "B162"),
    "404D6D": ("Nothing Ear (a)", "B162"), "839E9A": ("Nothing Ear (a)", "B162"),
    "FC3AAF": ("Nothing Ear (open)", "B174"), "CC3444": ("Nothing Ear (open)", "B174"),
    "7D46E5": ("Nothing Ear (3)", "B173"), "C1EBFD": ("Nothing Ear (3)", "B173"),
    "7B2328": ("Nothing Ear (3a)", "B190"), "E9C3BE": ("Nothing Ear (3a)", "B190"),
    "148887": ("Nothing Ear (3a)", "B190"), "DB45D3": ("Nothing Ear (3a)", "B190"),
    "C19ECD": ("Nothing Headphone (1)", "B170"), "2D6FDA": ("Nothing Headphone (1)", "B170"),
    "CDFDEC": ("Nothing Headphone (1) Pro", "B192"), "086485": ("Nothing Headphone (1) Pro", "B192"),
    "BFD53B": ("Nothing Headphone (a)", "B186"), "DE8953": ("Nothing Headphone (a)", "B186"),
    "97EF75": ("Nothing Headphone (a)", "B186"), "98D02B": ("Nothing Headphone (a)", "B186"),
    "6F6C71": ("Nothing Headphone (a)", "B186"), "810478": ("Nothing Headphone (a)", "B186"),
    "79B3A9": ("Nothing Headphone (a)", "B186"), "A292C6": ("Nothing Headphone (a)", "B186"),
    "5F8F82": ("CMF Buds Pro", "B163"), "2EB1CA": ("CMF Buds Pro", "B163"),
    "ADD2C4": ("CMF Buds Pro", "B163"),
    "4DFC4A": ("CMF Neckband Pro", "B164"), "26C190": ("CMF Neckband Pro", "B164"),
    "AE35FD": ("CMF Neckband Pro", "B164"),
    "D35E18": ("CMF Buds", "B168"), "ACCE54": ("CMF Buds", "B168"),
    "150A27": ("CMF Buds", "B168"),
    "F29566": ("CMF Buds Pro 2", "B172"), "CA36A6": ("CMF Buds Pro 2", "B172"),
    "A7B220": ("CMF Buds Pro 2", "B172"), "2B353E": ("CMF Buds Pro 2", "B172"),
    "2F45F5": ("CMF Buds Pro 2", "B172"), "E1BE45": ("CMF Buds Pro 2", "B172"),
    "1253C0": ("CMF Buds Pro 2", "B172"), "0F1A4F": ("CMF Buds Pro 2", "B172"),
    "F51D3D": ("CMF Buds Pro 2", "B195"), "A544CE": ("CMF Buds Pro 2", "B195"),
    "9EAC04": ("CMF Buds Pro 2", "B195"), "1D07D4": ("CMF Buds Pro 2", "B195"),
    "53BFE6": ("CMF Buds Pro 2", "B195"), "072714": ("CMF Buds Pro 2", "B195"),
    "19EF24": ("CMF Buds 2", "B179"), "FF2AB0": ("CMF Buds 2", "B179"),
    "D9AB5D": ("CMF Buds 2", "B179"), "3B1320": ("CMF Buds 2", "B197"),
    "359A7D": ("CMF Buds 2", "B197"), "00FDED": ("CMF Buds 2", "B197"),
    "5C587F": ("CMF Buds 2 Plus", "B184"), "4AEB6E": ("CMF Buds 2 Plus", "B184"),
    "70F8E3": ("CMF Buds 2a", "B185"), "ED5412": ("CMF Buds 2a", "B185"),
    "509CAE": ("CMF Buds 2a", "B185"),
    "563DA5": ("CMF Headphone Pro", "B175"), "73C9EB": ("CMF Headphone Pro", "B175"),
    "1EFB39": ("CMF Headphone Pro", "B175"),
    "8F28ED": ("CMF Clip Pro", "B189"), "E6673A": ("CMF Clip Pro", "B189"),
    "DCD2CB": ("CMF Clip Pro", "B189"), "DA1280": ("CMF Clip Pro", "B189"),
}

# Model base -> (noise control bitmask or -1, ultra bass, advanced EQ).
CAPS = {
    "B181": (53, False, False), "B157": (0, False, True), "B155": (63, False, True),
    "B162": (63, True, False), "B171": (63, True, True), "B163": (55, False, False),
    "B164": (63, True, False), "B168": (49, True, False), "B172": (63, True, False),
    "B174": (-1, False, True), "B170": (63, True, True), "B175": (63, False, False),
    "B185": (49, True, False), "B179": (63, True, False), "B184": (63, True, False),
    "B173": (63, True, True), "B186": (63, True, True), "B190": (63, False, True),
    "B189": (-1, True, False), "B192": (63, False, True), "B195": (63, True, False),
    "B197": (63, True, False),
}

ANC_TRANSPARENCY, ANC_STRENGTH, ANC_MID, ANC_ADAPTIVE = 0x10, 0x04, 0x02, 0x08
NO_IN_EAR = {"B174", "B185", "B175", "B189", "B186", "B164"}
THREE_STAGE_BASS = {"B189", "B186", "B195", "B197"}
RING_WHOLE_DEVICE = {"B170", "B164", "B186", "B192"}
# Presets verified for these models: 0 Balanced, 1 Voice, 2 More treble, 3 More bass.
PRESET_EQ = {"B155", "B171"}

# Noise control wire values.
ANC_OFF, ANC_TRANSPARENCY_MODE, ANC_LOW, ANC_MID_LEVEL, ANC_HIGH, ANC_ADAPTIVE_MODE = 5, 7, 3, 2, 1, 4

EQ_PRESETS = [(0, "Balanced"), (3, "More bass"), (2, "More treble"), (1, "Voice")]


def caps_for(base):
    return CAPS.get(base or "", (63, False, False))


def guess_from_name(name):
    for product, base in FAST_PAIR.values():
        if product.lower() == (name or "").lower():
            return product, base
    return None


def anc_options(base):
    level = caps_for(base)[0]
    if level <= 0:
        return []
    strength = (level & ANC_STRENGTH) != 0
    options = [{"value": ANC_OFF, "label": "Off"}]
    if level & ANC_TRANSPARENCY:
        options.append({"value": ANC_TRANSPARENCY_MODE, "label": "Transparency"})
    if level & ANC_ADAPTIVE:
        options.append({"value": ANC_ADAPTIVE_MODE, "label": "Adaptive"})
    if strength:
        options.append({"value": ANC_LOW, "label": "Noise cancelling: Low"})
    if level & ANC_MID:
        options.append({"value": ANC_MID_LEVEL, "label": "Noise cancelling: Mid"})
    options.append({"value": ANC_HIGH, "label": "Noise cancelling: High" if strength else "Noise cancelling"})
    return options


# ------------------------------------------------------------------ Protocol

GET_BATTERY, GET_ANC, SET_ANC, GET_EQ, SET_EQ = 0xC007, 0xC01E, 0xF00F, 0xC01F, 0xF010
GET_ADVANCED_EQ, SET_ADVANCED_EQ, GET_BASS, SET_BASS, RING = 0xC04C, 0xF04F, 0xC04E, 0xF051, 0xF002
GET_FIRMWARE, GET_IN_EAR, SET_IN_EAR, GET_LATENCY, SET_LATENCY = 0xC042, 0xC00E, 0xF004, 0xC041, 0xF040

CONTROL_SERVICE = "aeac4a03-dff5-498f-843a-34487cf133eb"
FAST_PAIR_SERVICE = "df21fe2c-2515-4fdb-8886-f12c4d67927c"


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_frame(command, op_id, payload=b""):
    head = bytes([0x55, 0x60, 0x01, command & 0xFF, command >> 8, len(payload), 0x00, op_id]) + bytes(payload)
    crc = crc16(head)
    return head + bytes([crc & 0xFF, crc >> 8])


def extract_frames(pending):
    """Pops complete frames off the bytearray. Returns [(command, payload, raw)]."""
    frames = []
    while pending:
        if pending[0] != 0x55:
            del pending[0]
            continue
        if len(pending) < 8:
            break
        # Header byte 1 holds control flags: 0x20 = a CRC follows the payload.
        # Replies carry a CRC; notifications the earbuds push on their own (55 00 01 ...) don't.
        has_crc = (pending[1] & 0x20) != 0
        length = pending[5] | (pending[6] << 8)
        total = 8 + length + (2 if has_crc else 0)
        if len(pending) < total:
            break
        raw = bytes(pending[:total])
        del pending[:total]
        frames.append((raw[3] | (raw[4] << 8), raw[8:8 + length], raw))
    return frames


def hexdump(data):
    return " ".join("%02x" % b for b in data)


# ------------------------------------------------------------------ Bluetooth (Linux)

AF_BLUETOOTH, BTPROTO_L2CAP, BTPROTO_RFCOMM = 31, 0, 3
SOCK_STREAM, SOCK_SEQPACKET = 1, 5
SDP_PSM = 1


class Link:
    """A connected Bluetooth socket: Python's own AF_BLUETOOTH support when the bundled Python has it,
    otherwise raw libc calls through ctypes."""

    def __init__(self, mac, proto, port, timeout):
        self.sock = None
        self.fd = -1
        if hasattr(socket, "AF_BLUETOOTH"):
            kind = socket.SOCK_SEQPACKET if proto == BTPROTO_L2CAP else socket.SOCK_STREAM
            s = socket.socket(socket.AF_BLUETOOTH, kind, proto)
            s.settimeout(timeout)
            try:
                s.connect((mac, port))
            except BaseException:
                s.close()
                raise
            s.settimeout(None)
            self.sock = s
        else:
            self.fd = _libc_connect(mac, proto, port, timeout)

    def fileno(self):
        return self.sock.fileno() if self.sock is not None else self.fd

    def write(self, data):
        if self.sock is not None:
            self.sock.sendall(data)
            return
        view = memoryview(data)
        while view:
            n = os.write(self.fd, view)
            view = view[n:]

    def read(self, timeout):
        """Returns bytes, b"" when the link closed, or None on timeout."""
        poller = select.poll()
        poller.register(self.fileno(), select.POLLIN | select.POLLHUP | select.POLLERR)
        if not poller.poll(int(timeout * 1000)):
            return None
        if self.sock is not None:
            return self.sock.recv(4096)
        return os.read(self.fd, 4096)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None
        elif self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def _bdaddr(mac):
    return bytes(int(x, 16) for x in reversed(mac.split(":")))


def _libc_connect(mac, proto, port, timeout):
    import ctypes
    import ctypes.util
    import fcntl
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    kind = SOCK_SEQPACKET if proto == BTPROTO_L2CAP else SOCK_STREAM
    fd = libc.socket(AF_BLUETOOTH, kind, proto)
    if fd < 0:
        e = ctypes.get_errno()
        raise OSError(e, "Bluetooth socket: " + os.strerror(e))
    try:
        if proto == BTPROTO_L2CAP:
            addr = struct.pack("<HH6sHB", AF_BLUETOOTH, port, _bdaddr(mac), 0, 0) + b"\0"   # sockaddr_l2
        else:
            addr = struct.pack("<H6sB", AF_BLUETOOTH, _bdaddr(mac), port) + b"\0"           # sockaddr_rc
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        buf = ctypes.create_string_buffer(addr, len(addr))
        if libc.connect(fd, buf, len(addr)) != 0:
            e = ctypes.get_errno()
            if e not in (errno.EINPROGRESS, errno.EAGAIN):
                raise OSError(e, os.strerror(e))
            poller = select.poll()
            poller.register(fd, select.POLLOUT)
            if not poller.poll(int(timeout * 1000)):
                raise TimeoutError("connect timed out")
            err = ctypes.c_int(0)
            size = ctypes.c_uint32(4)
            libc.getsockopt(fd, 1, 4, ctypes.byref(err), ctypes.byref(size))   # SOL_SOCKET, SO_ERROR
            if err.value:
                raise OSError(err.value, os.strerror(err.value))
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
        return fd
    except BaseException:
        os.close(fd)
        raise


# ---------- SDP: find the RFCOMM channel of a 128-bit service UUID

def parse_data_element(b, i=0):
    """Returns ((type, value), next index). Sequences/alternatives hold lists; uint/int/UUID hold ints."""
    h = b[i]
    t, s = h >> 3, h & 7
    i += 1
    if t == 0:
        return (0, None), i
    if s < 5:
        n = (1, 2, 4, 8, 16)[s]
    else:
        width = (1, 2, 4)[s - 5]
        n = int.from_bytes(b[i:i + width], "big")
        i += width
    raw = b[i:i + n]
    i += n
    if t in (6, 7):
        items, j = [], 0
        while j < len(raw):
            item, j = parse_data_element(raw, j)
            items.append(item)
        return (t, items), i
    if t in (1, 2, 3):
        return (t, int.from_bytes(raw, "big")), i
    return (t, raw), i


BLUETOOTH_BASE_UUID_LOW = 0x00001000800000805F9B34FB


def _short_uuid(value):
    if value < (1 << 32):
        return value
    if value & ((1 << 96) - 1) == BLUETOOTH_BASE_UUID_LOW:
        return value >> 96
    return None


def find_rfcomm_channel(element):
    t, value = element
    if t not in (6, 7):
        return None
    if len(value) >= 2 and value[0][0] == 3 and _short_uuid(value[0][1]) == 0x0003 and value[1][0] == 1:
        return value[1][1]
    for item in value:
        ch = find_rfcomm_channel(item)
        if ch is not None:
            return ch
    return None


def sdp_request(service_uuid, tid, continuation):
    pattern = b"\x35\x11\x1c" + bytes.fromhex(service_uuid.replace("-", ""))
    attributes = b"\x35\x03\x09\x00\x04"   # ProtocolDescriptorList only
    params = pattern + struct.pack(">H", 0xFFFF) + attributes + continuation
    return struct.pack(">BHH", 0x06, tid, len(params)) + params   # ServiceSearchAttributeRequest


def sdp_parse_response(resp):
    """Returns (attribute list bytes, continuation state incl. its length byte)."""
    if len(resp) < 7 or resp[0] != 0x07:
        raise OSError(errno.EPROTO, "SDP error response" if resp[:1] == b"\x01" else "unexpected SDP reply")
    count = struct.unpack(">H", resp[5:7])[0]
    data = resp[7:7 + count]
    clen = resp[7 + count]
    return data, resp[7 + count:8 + count + clen]


def rfcomm_channel(mac, service_uuid):
    link = Link(mac, BTPROTO_L2CAP, SDP_PSM, 10)
    try:
        data, continuation, tid = b"", b"\x00", 0
        while True:
            tid += 1
            link.write(sdp_request(service_uuid, tid, continuation))
            resp = link.read(5)
            if not resp:
                raise TimeoutError("no SDP reply")
            chunk, continuation = sdp_parse_response(resp)
            data += chunk
            if continuation[0] == 0:
                break
        if not data:
            return None
        return find_rfcomm_channel(parse_data_element(data)[0])
    finally:
        link.close()


# ---------- bluetoothctl

def system_env():
    # Decky's backend is a PyInstaller bundle; give system tools their own libraries back.
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=system_env()).stdout
    except (OSError, subprocess.SubprocessError) as e:
        decky.logger.warning("%s failed: %s", cmd, e)
        return ""


def device_info(mac):
    out = run(["bluetoothctl", "info", mac])
    info = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\w[\w ]*):\s*(.*)$", line)
        if m and m.group(1) not in info:
            info[m.group(1)] = m.group(2).strip()
    return info


def is_nothing_name(name):
    return (name or "").lower().startswith(("nothing", "cmf"))


def paired_devices():
    out = run(["bluetoothctl", "devices", "Paired"])
    if "Device " not in out:
        out = run(["bluetoothctl", "paired-devices"])
    result = []
    for line in out.splitlines():
        m = re.match(r"\s*Device ([0-9A-Fa-f:]{17}) (.*)$", line)
        if not m:
            continue
        mac, name = m.group(1).upper(), m.group(2).strip()
        info = device_info(mac)
        # Older bluetoothctl ignores the "Paired" filter and lists every known device.
        if info.get("Paired", "yes") != "yes" and info.get("Bonded", "no") != "yes":
            continue
        try:
            cls = int(info.get("Class", "0"), 16)
        except ValueError:
            cls = 0
        audio = ((cls >> 8) & 0x1F) == 0x04 or info.get("Icon", "").startswith("audio")
        if not (audio or is_nothing_name(name)):
            continue
        result.append({"address": mac, "name": info.get("Alias") or name, "connected": info.get("Connected") == "yes"})
    result.sort(key=lambda d: (not is_nothing_name(d["name"]), not d["connected"], d["name"].lower()))
    return result


# ---------- BlueZ change notifications (instead of polling bluetoothctl)

# Matched on BlueZ's object paths rather than sender='org.bluez': signals carry the unique bus name, and
# whether a monitor resolves well-known sender names depends on the bus implementation.
BLUEZ_MATCH = ("type='signal',path_namespace='/org/bluez',interface='org.freedesktop.DBus.Properties',"
               "member='PropertiesChanged',arg0='org.bluez.Device1'")


def bluez_monitor_cmd():
    # Watching the system bus needs root, which the plugin has (the "_root" flag).
    # stdbuf makes busctl write each event as it happens instead of buffering output to the pipe.
    cmd = ["busctl", "--system", "--json=short", "monitor", "--match=" + BLUEZ_MATCH]
    return (["stdbuf", "-oL"] if shutil.which("stdbuf") else []) + cmd


def parse_bluez_signal(line):
    """One `busctl --json=short monitor` line -> (mac, {property: value}) for a Device1 change, else None."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("member") != "PropertiesChanged":
        return None
    m = re.search(r"/dev_([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})$", msg.get("path") or "")
    data = (msg.get("payload") or {}).get("data")
    if not m or not isinstance(data, list) or len(data) < 2 or data[0] != "org.bluez.Device1" or not isinstance(data[1], dict):
        return None
    props = {k: (v.get("data") if isinstance(v, dict) and "data" in v else v) for k, v in data[1].items()}
    return m.group(1).replace("_", ":").upper(), props


# How long to wait before retrying a failed automatic connect, and how often to check when busctl isn't available.
RETRY_DELAYS = (15, 30, 60, 120)
POLL_FALLBACK = 30
SETTLE_DELAY = 2


# ------------------------------------------------------------------ Session

NOT_ANSWERING = "they didn't answer. Take them out of the case and make sure they're connected to the Deck."


def friendly_error(e):
    if isinstance(e, (TimeoutError, socket.timeout)):
        return NOT_ANSWERING
    if isinstance(e, OSError) and e.errno in (errno.EHOSTDOWN, errno.EHOSTUNREACH, errno.ETIMEDOUT):
        return NOT_ANSWERING
    if isinstance(e, OSError) and e.errno == errno.ECONNREFUSED:
        return "this device doesn't offer the Nothing control service."
    if isinstance(e, OSError) and e.errno == errno.EAFNOSUPPORT:
        return "Bluetooth isn't available on this system."
    return str(e) or e.__class__.__name__


def read_model_id(mac):
    """The Fast Pair channel announces the model ID right after connecting: 03 01 00 03 <id x3>."""
    channel = rfcomm_channel(mac, FAST_PAIR_SERVICE)
    if channel is None:
        return None
    link = Link(mac, BTPROTO_RFCOMM, channel, 5)
    try:
        data = b""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            chunk = link.read(max(0.05, deadline - time.monotonic()))
            if not chunk:
                break
            data += chunk
            i = data.find(b"\x03\x01\x00\x03")
            if i >= 0 and len(data) >= i + 7:
                return data[i + 4:i + 7].hex().upper()
        return data[4:7].hex().upper() if len(data) >= 7 else None
    finally:
        link.close()


class EarSession:
    def __init__(self, link, on_frame, on_closed, log):
        self.link = link
        self.on_frame = on_frame
        self.on_closed = on_closed
        self.log = log
        self.op_id = 0
        self.send_lock = threading.Lock()
        self.closed = False
        self.thread = threading.Thread(target=self._read_loop, name="ear-reader", daemon=True)

    def start(self):
        self.thread.start()

    def send(self, command, *payload):
        with self.send_lock:
            self.op_id = 1 if self.op_id >= 250 else self.op_id + 1
            frame = build_frame(command, self.op_id, bytes(payload))
            self.link.write(frame)
        self.log("TX " + hexdump(frame))

    def _read_loop(self):
        pending = bytearray()
        try:
            while not self.closed:
                chunk = self.link.read(1.0)
                if chunk is None:
                    continue
                if not chunk:
                    break
                pending.extend(chunk)
                for command, payload, raw in extract_frames(pending):
                    self.log("RX " + hexdump(raw))
                    try:
                        self.on_frame(command, payload)
                    except Exception as e:
                        decky.logger.warning("frame %04x: %s", command, e)
        except OSError:
            pass
        if not self.closed:
            self.on_closed(self)

    def close(self):
        self.closed = True
        self.link.close()


# ------------------------------------------------------------------ Plugin

def _empty_battery():
    return {"left": -1, "right": -1, "case": -1, "single": -1,
            "leftCharging": False, "rightCharging": False, "caseCharging": False, "singleCharging": False}


class Plugin:
    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.lock = threading.RLock()
        self.session = None
        self.settings_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
        self.traffic_path = os.path.join(decky.DECKY_PLUGIN_LOG_DIR, "traffic.log")
        self.settings = {"last_device": None, "last_case_level": -1, "last_case_time": 0}
        try:
            with open(self.settings_path) as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.suppressed = set()      # devices the user disconnected; left alone until they drop off Bluetooth
        self.retry = {}              # mac -> (failed attempts, monotonic time of the next attempt)
        self.paired_cache = []
        self.paired_time = -1e9
        self._reset_state()
        self.stopping = threading.Event()
        self.watcher = None
        self.monitor = self.loop.create_task(self._battery_timer())
        threading.Thread(target=self._watch_bluez, name="bluez-watch", daemon=True).start()
        self.loop.create_task(self._auto_connect())   # earbuds that were already connected before the plugin loaded
        decky.logger.info("Nothing Ear Control backend started")

    async def _unload(self):
        self.stopping.set()
        if getattr(self, "monitor", None):
            self.monitor.cancel()
        watcher = getattr(self, "watcher", None)
        if watcher is not None:
            try:
                watcher.terminate()
            except OSError:
                pass
        self._close_session()

    async def _uninstall(self):
        pass

    # ---------- state

    def _reset_state(self, status=""):
        with self.lock:
            self.state = {
                "connected": False, "connecting": False, "status": status, "error": False,
                "device": None, "model": None, "base": None, "firmware": None,
                "battery": _empty_battery(), "caseStale": None,
                "anc": None, "ancOptions": [], "eq": None, "eqNote": "", "hasEq": False,
                "bassOn": False, "bassLevel": 1, "hasBass": False, "bassMax": 5,
                "inEar": False, "hasInEar": False, "latency": False,
                "ringsAsOne": False, "ringingLeft": False, "ringingRight": False,
                "lastDevice": self.settings.get("last_device"),
            }

    def _status(self, text, error=False):
        with self.lock:
            self.state["status"] = text
            self.state["error"] = error

    def _save_settings(self):
        try:
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w") as f:
                json.dump(self.settings, f)
        except OSError as e:
            decky.logger.warning("couldn't save settings: %s", e)

    def _log_traffic(self, line):
        try:
            with open(self.traffic_path, "a") as f:
                f.write(time.strftime("%H:%M:%S") + "  " + line + "\n")
        except OSError:
            pass

    # ---------- frames from the earbuds (reader thread)

    def _on_frame(self, command, p):
        with self.lock:
            s = self.state
            if command in (0xE001, 0x4007) and p:
                b = _empty_battery()
                for i in range(p[0]):
                    if 2 + i * 2 >= len(p):
                        break
                    ident, v = p[1 + i * 2], p[2 + i * 2]
                    level, charging = v & 0x7F, (v & 0x80) != 0
                    key = {2: "left", 3: "right", 4: "case", 6: "single"}.get(ident)
                    if key:
                        b[key] = level
                        b[key + "Charging"] = charging
                s["battery"] = b
                if b["case"] >= 0:
                    s["caseStale"] = None
                    if b["case"] != self.settings["last_case_level"] or time.time() - self.settings["last_case_time"] >= 300:
                        self.settings["last_case_level"] = b["case"]
                        self.settings["last_case_time"] = time.time()
                        self._save_settings()
                elif self.settings["last_case_level"] >= 0:
                    s["caseStale"] = {"level": self.settings["last_case_level"], "time": self.settings["last_case_time"]}
            elif command in (0x401E, 0xE003, 0x6003) and len(p) > 1:
                s["anc"] = p[1]
            elif command in (0x401F, 0x4050) and p:
                s["eq"] = p[0]
                known = any(v == p[0] for v, _ in EQ_PRESETS)
                s["eqNote"] = "" if known else "A custom EQ from the Nothing X app is active. Pick a preset to replace it."
            elif command == 0x404C and p:
                if p[0] == 1:
                    s["eq"] = None
                    s["eqNote"] = "Advanced EQ from the Nothing X app is active. Pick a preset to replace it."
            elif command == 0x404E and len(p) > 1:
                three = s["base"] in THREE_STAGE_BASS
                s["bassOn"] = p[0] == 1
                s["bassLevel"] = max(1, p[1] // 5 if three else p[1] // 2)
            elif command == 0x400E and len(p) > 2:
                s["inEar"] = p[2] == 1
            elif command == 0x4041 and p:
                s["latency"] = p[0] == 1
            elif command == 0x4042:
                s["firmware"] = p.decode("ascii", "replace").strip("\0 ")

    def _on_closed(self, session):
        with self.lock:
            if self.session is not session:
                return
            self.session = None
        session.close()
        self._reset_state("The earbuds disconnected.")
        self._status("The earbuds disconnected.", True)

    # ---------- connection

    def _connect_blocking(self, mac, name):
        info = device_info(mac)
        if info.get("Connected") != "yes":
            self._status("Connecting " + name + " over Bluetooth…")
            run(["bluetoothctl", "connect", mac], timeout=25)
        self._status("Talking to " + name + "…")
        found = None
        try:
            model_id = read_model_id(mac)
            if model_id:
                found = FAST_PAIR.get(model_id)
        except Exception as e:
            decky.logger.info("model id: %s", e)
        if found is None:
            found = guess_from_name(name)
        channel = rfcomm_channel(mac, CONTROL_SERVICE)
        if channel is None:
            raise OSError(errno.ECONNREFUSED, "no control service")
        return found, Link(mac, BTPROTO_RFCOMM, channel, 10)

    async def _connect(self, mac, auto=False):
        """Returns True once connected."""
        # Only the event loop thread starts connections, so this check-then-set can't race.
        if self.session is not None or self.state["connecting"]:
            return False
        with self.lock:
            self.state["connecting"] = True
        name = mac
        for d in await self._paired(max_age=0):
            if d["address"] == mac:
                name = d["name"]
        with self.lock:
            self.state["device"] = {"address": mac, "name": name}
        self._status("Connecting to " + name + "…")
        try:
            found, link = await self.loop.run_in_executor(None, self._connect_blocking, mac, name)
        except Exception as e:
            decky.logger.info("connect failed: %r", e)
            self._reset_state()
            self._status("Couldn't connect to " + name + ": " + friendly_error(e) +
                         (" Trying again automatically." if auto else ""), True)
            return False

        product, base = found if found else (name, None)
        try:
            with open(self.traffic_path, "w") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  connected to " + name +
                        (" (" + product + ", " + base + ")" if base else " (unknown model)") + "\n")
        except OSError:
            pass
        self._reset_state()
        level, ultra_bass, advanced_eq = caps_for(base)
        with self.lock:
            s = self.state
            s.update({
                "connected": True, "device": {"address": mac, "name": name}, "model": product, "base": base,
                "ancOptions": anc_options(base), "hasEq": base in PRESET_EQ, "hasBass": ultra_bass,
                "bassMax": 2 if base in THREE_STAGE_BASS else 5, "hasInEar": base not in NO_IN_EAR,
                "ringsAsOne": base == "B181" or base in RING_WHOLE_DEVICE,
                "advancedEq": advanced_eq,
            })
            if self.settings["last_case_level"] >= 0:
                s["caseStale"] = {"level": self.settings["last_case_level"], "time": self.settings["last_case_time"]}
            self.session = EarSession(link, self._on_frame, self._on_closed, self._log_traffic)
            self.settings["last_device"] = mac
            s["lastDevice"] = mac
        self._save_settings()
        self.session.start()
        self._status("Connected to " + name + "." if base else "Connected. Unknown model — showing common controls.")
        await self._query_all()
        return True

    async def _query_all(self):
        with self.lock:
            s = self.state
            queries = [GET_BATTERY, GET_FIRMWARE, GET_LATENCY]
            if s["ancOptions"]:
                queries.append(GET_ANC)
            if s["hasEq"]:
                queries.append(GET_EQ)
                if s.get("advancedEq"):
                    queries.append(GET_ADVANCED_EQ)
            if s["hasBass"]:
                queries.append(GET_BASS)
            if s["hasInEar"]:
                queries.append(GET_IN_EAR)
            session = self.session
        for q in queries:
            if self.session is not session:
                return
            self._send(q)
            await asyncio.sleep(0.12)

    def _send(self, command, *payload):
        session = self.session
        if session is None:
            self._status("Not connected to the earbuds.", True)
            return False
        try:
            session.send(command, *payload)
            return True
        except OSError as e:
            self._status("Couldn't send the command: " + friendly_error(e), True)
            return False

    def _close_session(self):
        with self.lock:
            session, self.session = self.session, None
        if session is not None:
            session.close()

    async def _paired(self, max_age=60):
        if time.monotonic() - self.paired_time > max_age:
            self.paired_cache = await self.loop.run_in_executor(None, paired_devices)
            self.paired_time = time.monotonic()
        return self.paired_cache

    # Connects by itself to Nothing / CMF earbuds (or the last used device) that are connected to this machine.
    # Runs when BlueZ reports a connection, at startup and when the panel opens; nothing polls while idle.
    # A failed attempt (e.g. still in the case) is retried after 15 s, 30 s, 60 s, then every 2 minutes.
    async def _auto_connect(self, now=False):
        if self.session is not None or self.state["connecting"]:
            return
        last = self.settings.get("last_device")
        candidates = [d["address"] for d in await self._paired() if is_nothing_name(d["name"]) or d["address"] == last]
        candidates.sort(key=lambda mac: mac != last)
        for mac in candidates:
            info = await self.loop.run_in_executor(None, device_info, mac)
            if info.get("Connected") != "yes":
                self.suppressed.discard(mac)
                self.retry.pop(mac, None)
                continue
            if mac in self.suppressed:
                continue
            count, due = self.retry.get(mac, (0, 0))
            if not now and time.monotonic() < due:
                continue
            if await self._connect(mac, auto=True):
                self.retry.pop(mac, None)
            else:
                delay = RETRY_DELAYS[min(count, len(RETRY_DELAYS) - 1)]
                self.retry[mac] = (count + 1, time.monotonic() + delay)
                self.loop.call_later(delay, self._spawn_auto_connect)
            return

    def _spawn_auto_connect(self, now=False):
        self.loop.create_task(self._auto_connect(now))

    # From the BlueZ watcher, on the event loop.
    def _on_bluez(self, mac, props):
        if mac not in [d["address"] for d in self.paired_cache]:
            self.paired_time = -1e9   # a device we haven't listed yet (just paired)
        if props.get("Connected") is False:
            self.suppressed.discard(mac)
            self.retry.pop(mac, None)
        elif props.get("Connected") is True or props.get("ServicesResolved") is True:
            # Let the earbuds finish setting up audio before opening the control channel.
            self.loop.call_later(SETTLE_DELAY, self._spawn_auto_connect, True)

    # Runs `busctl monitor` for BlueZ device changes; if it's missing or stops, checks every 30 s until it's back.
    def _watch_bluez(self):
        warned = False
        while not self.stopping.is_set():
            problem = None
            try:
                proc = subprocess.Popen(bluez_monitor_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, env=system_env())
            except OSError as e:
                proc, problem = None, str(e)
            if proc is not None:
                self.watcher = proc
                events = 0
                for line in proc.stdout:
                    parsed = parse_bluez_signal(line)
                    if parsed:
                        events += 1
                        self.loop.call_soon_threadsafe(self._on_bluez, *parsed)
                proc.wait()
                self.watcher = None
                if self.stopping.is_set():
                    return
                problem = "busctl monitor exited with code %s after %d events" % (proc.returncode, events)
            if not warned:
                decky.logger.warning("can't watch BlueZ (%s); checking every %d s instead", problem, POLL_FALLBACK)
                warned = True
            # Catch anything missed while not watching.
            self.loop.call_soon_threadsafe(self._spawn_auto_connect)
            self.stopping.wait(POLL_FALLBACK)

    # Battery every minute while connected.
    async def _battery_timer(self):
        while True:
            await asyncio.sleep(60)
            if self.session is not None:
                self._send(GET_BATTERY)

    # ---------- called from the frontend

    async def get_state(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    async def list_devices(self):
        return await self._paired(max_age=0)

    # The panel was opened: look for earbuds right away instead of waiting for a retry.
    async def check_now(self):
        self._spawn_auto_connect(True)

    async def connect(self, mac):
        self.suppressed.discard(mac)
        self.loop.create_task(self._connect(mac))

    async def disconnect(self):
        with self.lock:
            device = self.state["device"]
        if device:
            self.suppressed.add(device["address"])
        self._close_session()
        self._reset_state("Disconnected. Ear Control won't reconnect until the earbuds reconnect to this device.")

    async def set_anc(self, wire):
        if self._send(SET_ANC, 0x01, wire, 0x00):
            with self.lock:
                self.state["anc"] = wire
                label = next((o["label"] for o in self.state["ancOptions"] if o["value"] == wire), "")
            self._status("Noise control: " + label + ".")

    async def set_eq(self, preset):
        if self.state.get("advancedEq"):
            self._send(SET_ADVANCED_EQ, 0x00, 0x00)
        if self._send(SET_EQ, preset, 0x00):
            with self.lock:
                self.state["eq"] = preset
                self.state["eqNote"] = ""
            self._status("Equalizer: " + dict(EQ_PRESETS).get(preset, "") + ".")

    async def set_bass(self, on, level):
        with self.lock:
            three = self.state["base"] in THREE_STAGE_BASS
            level = max(1, min(self.state["bassMax"], int(level)))
        wire = (level * 5 if three else level * 2) if on else 0
        if self._send(SET_BASS, 1 if on else 0, wire):
            with self.lock:
                self.state["bassOn"] = bool(on)
                self.state["bassLevel"] = level
            self._status("Bass enhance level %d." % level if on else "Bass enhance off.")

    async def set_in_ear(self, on):
        if self._send(SET_IN_EAR, 1, 1, 1 if on else 0):
            with self.lock:
                self.state["inEar"] = bool(on)
            self._status("In-ear detection " + ("on." if on else "off."))

    async def set_latency(self, on):
        if self._send(SET_LATENCY, 1 if on else 2, 0):
            with self.lock:
                self.state["latency"] = bool(on)
            self._status("Low latency mode " + ("on." if on else "off."))

    def _ring_side(self, ring, left):
        base = self.state["base"]
        on = 1 if ring else 0
        if base == "B181":
            return self._send(RING, on)
        if base in RING_WHOLE_DEVICE:
            return self._send(RING, 0x06, on)
        return self._send(RING, 0x02 if left else 0x03, on)

    # side: "left", "right" or "both". The earbuds have no "ring both" command, so ring each side.
    async def ring(self, side, on):
        with self.lock:
            one = self.state["ringsAsOne"]
        sides = [True] if one else ([True, False] if side == "both" else [side == "left"])
        for left in sides:
            if self._ring_side(bool(on), left):
                with self.lock:
                    self.state["ringingLeft" if left else "ringingRight"] = bool(on)
        if on:
            self._status("Ringing… press Stop ringing to end.")
        else:
            self._status("Stopped ringing.")
