"""Constants for the Lithe Audio integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "lithe_audio"
MANUFACTURER: Final = "Lithe Audio"

# ── Config flow keys ───────────────────────────────────────────────────────
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_NAME: Final = "name"
CONF_PLATFORM: Final = "platform"           # "LS9" or "LS10"
CONF_MODEL: Final = "model"
CONF_CERT_PEM: Final = "cert_pem"           # contents of client.pem (LS10)
CONF_CERT_KEY: Final = "cert_key"           # contents of client.key (LS10)
CONF_MAC: Final = "mac"

DEFAULT_PORT: Final = 7777
DEFAULT_NAME: Final = "Lithe Audio Speaker"

# ── Platforms ──────────────────────────────────────────────────────────────
PLATFORM_LS9: Final = "LS9"
PLATFORM_LS10: Final = "LS10"

# ── Product models ─────────────────────────────────────────────────────────
# Used to scope feature exposure (chime count, EQ, etc.)
MODEL_PRO2: Final = "PRO2"
MODEL_V3: Final = "WiFiV3"
MODEL_IO1: Final = "iO1"
MODEL_V2: Final = "WiFiV2"
MODEL_PRO: Final = "PRO"
MODEL_MICRO: Final = "MicroSub"
MODEL_GENERIC: Final = "Generic"

LS10_MODELS: Final = {MODEL_PRO2, MODEL_V3, MODEL_IO1}
LS9_MODELS: Final = {MODEL_V2, MODEL_PRO, MODEL_MICRO}

# Chime counts per model (embedded /system/usr/song[N].mp3)
PRODUCT_CHIMES: Final[dict[str, int]] = {
    MODEL_PRO2: 15,
    MODEL_V3: 15,
    MODEL_IO1: 10,
    MODEL_V2: 10,
    MODEL_PRO: 6,
    MODEL_MICRO: 0,
    MODEL_GENERIC: 15,
}

# ── LUCI Message Box IDs ───────────────────────────────────────────────────
MB_REGISTER: Final = 3
MB_FIRMWARE: Final = 5
MB_REBOOT: Final = 37
MB_TRANSPORT: Final = 40           # PLAY/PAUSE/STOP/NEXT/PREV/SEEK/RESUME
MB_BROWSE: Final = 41              # SELECTITEM/PLAYITEM/SCROLL
MB_NOW_PLAYING: Final = 42         # UI JSON (push)
MB_POSITION: Final = 49            # ms position
MB_SOURCE: Final = 50              # numeric source ID
MB_PLAY_STATE: Final = 51          # 0/1/2/3/5
MB_MUTE: Final = 63                # MUTE/UNMUTE
MB_VOLUME: Final = 64              # 0..100
MB_PRESET: Final = 70              # FAV_SAVE/FAV_PLAY/FAV_DELETE/FAV_LIST
MB_CHIME: Final = 80               # "play N"
MB_DEVICE_NAME: Final = 90
MB_NETWORK_INFO: Final = 91
MB_DEVICE_DETAILS: Final = 92      # JSON: mac/serial/version
MB_INPUT_START: Final = 95         # AUX/Line-in
MB_INPUT_STOP: Final = 96
MB_TUNNEL_START: Final = 111
MB_TUNNEL_STOP: Final = 121
MB_DEVICE_INFO: Final = 208        # Model:xxx etc
MB_BLUETOOTH: Final = 209          # ON/OFF/ENTPAIR/DISCONNECT
MB_BT_STATUS: Final = 210
MB_SERVICE_CREDS: Final = 213
MB_TIMEZONE: Final = 573
MB_CAST_STATUS: Final = 572

# ── Protocol command types ────────────────────────────────────────────────
CMD_GET: Final = 0x01
CMD_SET: Final = 0x02

REMOTE_ID: Final = 0xAAAA
PACKET_TERMINATOR: Final = b"\x00"

# ── Transport commands (MB#40 payload) ────────────────────────────────────
TRANSPORT_PLAY: Final = "PLAY"
TRANSPORT_PAUSE: Final = "PAUSE"
TRANSPORT_STOP: Final = "STOP"
TRANSPORT_NEXT: Final = "NEXT"
TRANSPORT_PREV: Final = "PREV"
TRANSPORT_RESUME: Final = "RESUME"
TRANSPORT_MUTE: Final = "MUTE"
TRANSPORT_UNMUTE: Final = "UNMUTE"

# ── Mute commands (MB#63) ─────────────────────────────────────────────────
MUTE_ON: Final = "MUTE"
MUTE_OFF: Final = "UNMUTE"
MUTE_TOGGLE: Final = "MUTETOGGLE"

# ── Play states (MB#51 payload values) ────────────────────────────────────
# Note: the API doc gives both "0=Playing 1=Paused 5=Buffering" (partner doc)
# and "0=Playing 1=Stopped 2=Paused 3=Connecting" (LUCI tech note); we map
# both representations here for safety.
PLAY_STATES: Final[dict[str, str]] = {
    "0": "playing",
    "1": "paused",     # partner doc; bridge.py maps 1=stopped — see _coerce_state
    "2": "paused",
    "3": "buffering",
    "5": "buffering",
}

# ── Source ID map (MB#50 numeric IDs, LS10 platform) ──────────────────────
SOURCE_NAMES: Final[dict[int, str]] = {
    0: "No Source",
    1: "AirPlay",
    2: "DMR",
    3: "DMP",
    4: "Spotify",
    5: "USB",
    7: "Melon",
    8: "vTuner",
    9: "TuneIn",
    11: "Playlist",
    13: "AUX In",
    14: "SPDIF",
    17: "Direct URL",
    18: "QPlay",
    19: "Bluetooth",
    21: "Deezer",
    22: "Tidal",
    23: "Favourites",
    24: "Google Cast",
    25: "External",
    26: "RTSP",
    27: "Roon",
    28: "Alexa",
    30: "Airable",
    31: "Democloud",
}

# Sources that are essentially read-only inputs (no transport control useful)
INPUT_ONLY_SOURCES: Final = {13, 14, 19, 25}     # AUX/SPDIF/Bluetooth/External
# Sources where SEEK is reliable
SEEKABLE_SOURCES: Final = {4, 17, 21, 22, 30}    # Spotify/DirectUrl/Deezer/Tidal/Airable

# ── Bluetooth commands (MB#209) ───────────────────────────────────────────
BT_ON: Final = "ON"
BT_OFF: Final = "OFF"
BT_PAIR: Final = "ENTPAIR"
BT_DISCONNECT: Final = "DISCONNECT"
BT_GET_ADDR: Final = "GETLOCALBTADDR"

# ── Update intervals & timing ─────────────────────────────────────────────
RECONNECT_BASE_DELAY: Final = 2.0      # seconds, exponential backoff base
RECONNECT_MAX_DELAY: Final = 60.0
KEEPALIVE_INTERVAL: Final = 25.0       # seconds — speaker pushes MB#51 ~30s
VOLUME_DEBOUNCE_MS: Final = 250
CONNECT_TIMEOUT: Final = 10.0
DEFAULT_SCAN_INTERVAL: Final = 30      # for state polling fallback

# ── Discovery ─────────────────────────────────────────────────────────────
LSSDP_MULTICAST_ADDR: Final = "239.255.255.250"
LSSDP_PORT: Final = 1800        # Lithe LSSDP uses 1800, not 1900
LSSDP_MSEARCH: Final = (
    b"M-SEARCH * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1800\r\n"
    b"\r\n"
    b"PROTOCOL: Version 1.0\r\n"
)

# Zeroconf service type (some devices also announce on standard mDNS)
ZEROCONF_TYPE: Final = "_googlecast._tcp.local."

# ── HA-side identifiers ───────────────────────────────────────────────────
SIGNAL_STATE_UPDATED: Final = f"{DOMAIN}_state_updated"
SIGNAL_GROUPS_UPDATED: Final = f"{DOMAIN}_groups_updated"

# ── Service names ─────────────────────────────────────────────────────────
SERVICE_PLAY_CHIME: Final = "play_chime"
SERVICE_PLAY_PRESET: Final = "play_preset"
SERVICE_SAVE_PRESET: Final = "save_preset"
SERVICE_DELETE_PRESET: Final = "delete_preset"
SERVICE_PLAY_DIRECT: Final = "play_direct"
SERVICE_SEND_RAW: Final = "send_raw_command"
SERVICE_REBOOT: Final = "reboot"

ATTR_CHIME_INDEX: Final = "chime_index"
ATTR_PRESET_SLOT: Final = "preset_slot"
ATTR_DIRECT_PATH: Final = "path"
ATTR_RAW_MBID: Final = "mbid"
ATTR_RAW_PAYLOAD: Final = "payload"
ATTR_RAW_CMD_TYPE: Final = "cmd_type"
