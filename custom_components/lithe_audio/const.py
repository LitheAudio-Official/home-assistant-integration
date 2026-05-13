"""Constants for the Lithe Audio integration."""

DOMAIN = "lithe_audio"

# Config entry keys
CONF_HOST       = "host"
CONF_PORT       = "port"
CONF_PRODUCT    = "product"
CONF_USE_TLS    = "use_tls"
CONF_CERT_PATH  = "cert_path"
CONF_KEY_PATH   = "key_path"

# Default values
DEFAULT_PORT = 7777
DEFAULT_TLS  = True

# ── Products ────────────────────────────────────────────────────────────────
PRODUCT_PRO2   = "pro2"
PRODUCT_V3     = "wifiv3"
PRODUCT_IO1    = "io1"
PRODUCT_V2     = "wifiv2"
PRODUCT_PRO    = "wifipro"
PRODUCT_MICRO  = "micro"

PRODUCT_NAMES = {
    PRODUCT_PRO2:  "WiFi PRO 2",
    PRODUCT_V3:    "WiFi Speaker V3",
    PRODUCT_IO1:   "iO1",
    PRODUCT_V2:    "WiFi Speaker V2",
    PRODUCT_PRO:   "WiFi PRO",
    PRODUCT_MICRO: "Micro Subwoofer",
}

# LS10 = TLS 1.2, LS9 = plain TCP
LS10_PRODUCTS = {PRODUCT_PRO2, PRODUCT_V3, PRODUCT_IO1}
LS9_PRODUCTS  = {PRODUCT_V2, PRODUCT_PRO, PRODUCT_MICRO}

# ── Sources (MB#50 payload) ─────────────────────────────────────────────────
# Corrected to match LUCI API spec (v15.0.7)
SOURCES = {
    0:  "No Source",
    1:  "AirPlay",
    2:  "DMR",
    3:  "DMP",
    4:  "Spotify",
    5:  "USB",
    7:  "Melon",
    8:  "vTuner",
    9:  "TuneIn",
    11: "Playlist",
    13: "AUX In",
    14: "SPDIF In",
    17: "Direct URL",
    18: "QPlay",
    19: "Bluetooth",
    21: "Deezer",
    22: "Tidal",
    23: "Favourites",
    24: "Google Cast",
    27: "Roon",
    28: "Alexa",
    30: "Airable",
}

# Sources actually supported per product (used for source_list)
PRODUCT_SOURCES = {
    PRODUCT_PRO2:  [0, 1, 4, 9, 13, 14, 19, 21, 22, 23, 24, 27, 28, 30],
    PRODUCT_V3:    [0, 1, 4, 9, 19, 21, 22, 23, 24, 27, 28, 30],
    PRODUCT_IO1:   [0, 1, 4, 9, 21, 22, 23, 24, 27, 28, 30],
    PRODUCT_V2:    [0, 1, 4, 19, 24, 30],
    PRODUCT_PRO:   [0, 1, 4, 24, 30],
    PRODUCT_MICRO: [0, 1, 24],
}

# ── Message Box IDs ─────────────────────────────────────────────────────────
MB_REGISTER       = 3
MB_FIRMWARE       = 5
MB_HOST_PRESENT   = 9
MB_PLAYBACK_AUTH  = 10    # HOST MCU only — NEVER respond with MB#11
MB_TRANSPORT      = 40
MB_BROWSE         = 41
MB_NOW_PLAYING    = 42
MB_ARTWORK        = 43
MB_POSITION       = 49
MB_SOURCE         = 50
MB_PLAY_STATE     = 51
MB_MUTE           = 63
MB_VOLUME         = 64
MB_FAVOURITES     = 70
MB_CHIME          = 80
MB_AUDIOCUE       = 82   # NEW: Audiocue lifecycle notifications (newer firmware)
                          # Speaker→host. Payloads observed:
                          #   "AUDIOCUE_START"  — chime is about to play,
                          #                       speaker pauses any music
                          #   "SUCCESS"         — chime finished, music resumes
                          #   "FAILURE" / "NI"  — slot empty or playback failed
MB_DEVICE_NAME    = 90
MB_NETWORK_INFO   = 91
MB_DSP            = 112
MB_REBOOT_REQ     = 114   # Reboot Request (was incorrectly 37)
MB_REBOOT_CMD     = 115
MB_INTERFACE_IP   = 123   # RxTx_MB#123 — current network interface + IP address
MB_NETWORK_STATUS = 124   # RxTx_MB#124 — WLAN/ETH/P2P active interface status
MB_FACTORY_RESET  = 150
MB_RSSI           = 151   # RxTx_MB#151 — WiFi signal strength (dBm)
MB_DEVICE_INFO    = 208   # Also used for NV Read/Write (READ_<NVitem>)
MB_BLUETOOTH      = 209
MB_BT_STATUS      = 210
MB_CAST_STATUS    = 572
MB_TIMEZONE       = 573

# ── MB#124 active network values ────────────────────────────────────────────
NETWORK_STATUS = {
    "1": "WLAN",
    "2": "Ethernet",
    "3": "P2P",
    "4": "WAC/SAC/LS-Connect",
}

# ── Transport commands (MB#40 payload) ──────────────────────────────────────
TRANSPORT_PLAY    = "PLAY"
TRANSPORT_PAUSE   = "PAUSE"
TRANSPORT_STOP    = "STOP"
TRANSPORT_RESUME  = "RESUME"
TRANSPORT_NEXT    = "NEXT"
TRANSPORT_PREV    = "PREV"

# ── Play states (MB#51 payload) ─────────────────────────────────────────────
PLAY_STATES = {
    "0": "playing",
    "1": "stopped",
    "2": "paused",
    "3": "connecting",
    "4": "buffering",   # receiving
    "5": "buffering",
}

# ── Mute commands (MB#63 payload) ───────────────────────────────────────────
MUTE_ON     = "MUTE"
MUTE_OFF    = "UNMUTE"
MUTE_TOGGLE = "MUTETOGGLE"

# ── Bluetooth commands (MB#209 payload) ─────────────────────────────────────
BT_ON      = "ON"
BT_OFF     = "OFF"
BT_PAIR    = "ENTPAIR"
BT_DISC    = "DISCONNECT"

# ── DSP sub-MB IDs (LS10 MB#112 tunnel) ─────────────────────────────────────
# Confirmed from live capture: PRO2 firmware CR443GP_3713
# ── DSP sub-MB IDs (tunneled inside MB#112) ─────────────────────────────────
# Verified against real firmware CR443GP_3713 packet captures.
DSP_EQ        = 0x0A   # 0=Normal 1=Acoustic 2=Jazz 3=Pop 4=HipHop (confirmed)
DSP_OUTPUT    = 0x09   # 0=Stereo 1=Mono 2=Left 3=Right (firmware uses values 2-6)
DSP_NIGHTMODE = 0x0C   # 0=OFF 1=ON (toggle observed in capture)
DSP_LOUDNESS  = 0x0D   # PRO2: signed byte -10..+10  |  V3/iO1: 0=OFF 1=ON (toggle observed)
DSP_HIGHPASS  = 0x0F   # 0=OFF 1=60Hz 2=80Hz 3=100Hz 4=120Hz (toggle observed at 0x0F)
DSP_TUNING    = 0x15   # 0=13L Enclosure 1=Open Back  (PRO2 only, observed at 0x15)
DSP_BALANCE   = 0x29   # signed byte -6..+6  (observed at 0x29)

EQ_PRESETS  = ["Normal", "Acoustic", "Jazz", "Pop", "Hip-Hop"]
HP_OPTIONS  = ["OFF", "60Hz", "80Hz", "100Hz", "120Hz"]
OUT_OPTIONS = ["Stereo", "Mono", "Left", "Right"]

# ── Per-product chime counts ────────────────────────────────────────────────
PRODUCT_CHIMES = {
    PRODUCT_PRO2:  15,
    PRODUCT_V3:    6,
    PRODUCT_IO1:   10,
    PRODUCT_V2:    0,
    PRODUCT_PRO:   6,
    PRODUCT_MICRO: 0,
}

# ── Per-product capability matrix ───────────────────────────────────────────
# Single source of truth for which entities get created per product.
# Every entity platform reads from this — no scattered ``if product in (…)``
# checks anywhere else.
#
# Capability keys:
#   chimes           — number of chime slots (0 = no chime buttons)
#   eq_select        — EQ preset selector
#   output_select    — Stereo/Mono/Left/Right selector
#   highpass_select  — HPF frequency selector (PRO2 only)
#   tuning_select    — 13L Enclosure / Open Back (PRO2 only)
#   balance_number   — -6..+6 balance slider
#   loudness_number  — -10..+10 dB slider (PRO2 only)
#   loudness_switch  — on/off loudness (V3/iO1/V2/PRO)
#   nightmode_switch — Night Mode on/off
#   bluetooth_switch — BT on/off + pair/disconnect (all products)
PRODUCT_CAPS = {
    PRODUCT_PRO2: {
        "chimes":           15,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  True,
        "tuning_select":    True,
        "balance_number":   True,
        "loudness_number":  True,
        "loudness_switch":  False,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_V3: {
        "chimes":           6,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_IO1: {
        "chimes":           10,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_V2: {
        "chimes":           0,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_PRO: {
        "chimes":           6,
        "eq_select":        True,
        "output_select":    True,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   True,
        "loudness_number":  False,
        "loudness_switch":  True,
        "nightmode_switch": True,
        "bluetooth_switch": True,
    },
    PRODUCT_MICRO: {
        "chimes":           0,
        "eq_select":        False,
        "output_select":    False,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   False,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": True,    # BT on; no DSP for now
    },
}


def caps(product: str) -> dict:
    """Return the capability dict for a product, or an all-False dict."""
    return PRODUCT_CAPS.get(product, {
        "chimes":           0,
        "eq_select":        False,
        "output_select":    False,
        "highpass_select":  False,
        "tuning_select":    False,
        "balance_number":   False,
        "loudness_number":  False,
        "loudness_switch":  False,
        "nightmode_switch": False,
        "bluetooth_switch": False,
    })

# ── LSSDP discovery ─────────────────────────────────────────────────────────
LSSDP_MULTICAST_ADDR = "239.255.255.250"
LSSDP_PORT           = 1800
LSSDP_MSEARCH = (
    b"M-SEARCH * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1800\r\n"
    b"MAN: \"ssdp:discover\"\r\n"
    b"MX: 3\r\n"
    b"ST: urn:LinkPlay:device:LinkPlay:1\r\n\r\n"
)

# Platform labels (used by discovery)
PLATFORM_LS9   = "LS9"
PLATFORM_LS10  = "LS10"

# LS10 model name fragments (used to classify LSSDP responses)
LS10_MODELS = ("PRO2", "WiFiV3", "WIFIV3", "IO1", "io1", "iO1")

# ── Bundled client certificate ──────────────────────────────────────────────
# LS10 speakers use TLS 1.2 mutual auth with a single per-developer cert
# issued by Lithe Audio. The cert is bundled with the integration so users
# never have to obtain or paste it.
import os as _os
_CERTS_DIR = _os.path.join(_os.path.dirname(__file__), "certs")
BUNDLED_CERT_PEM = _os.path.join(_CERTS_DIR, "client.pem")
BUNDLED_CERT_KEY = _os.path.join(_CERTS_DIR, "client.key")

# ── Update intervals ────────────────────────────────────────────────────────
SCAN_INTERVAL_S = 30

# ── Coordinator data keys ───────────────────────────────────────────────────
DATA_COORDINATOR = "coordinator"
DATA_DEVICE_INFO = "device_info"
DATA_TANNOY_SAVED = "tannoy_saved"
DATA_PRAYER       = "prayer"

# ── Prayer scheduler ────────────────────────────────────────────────────────
ALADHAN_URL = "https://api.aladhan.com/v1/timingsByCity"

PRAYER_NAMES = [
    "fajr", "sunrise", "dhuhr", "asr", "sunset", "maghrib", "isha", "midnight",
]
