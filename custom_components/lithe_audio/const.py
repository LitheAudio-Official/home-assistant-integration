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
MB_DEVICE_NAME    = 90
MB_NETWORK_INFO   = 91
MB_DSP            = 112
MB_REBOOT_REQ     = 114   # Reboot Request (was incorrectly 37)
MB_REBOOT_CMD     = 115
MB_FACTORY_RESET  = 150
MB_DEVICE_INFO    = 208
MB_BLUETOOTH      = 209
MB_BT_STATUS      = 210
MB_CAST_STATUS    = 572
MB_TIMEZONE       = 573

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
DSP_EQ        = 0x0A   # 0=Normal 1=Acoustic 2=Jazz 3=Pop 4=HipHop
DSP_LOUDNESS  = 0x16   # PRO2: signed byte -10..+10  |  V3/iO1: 0=OFF 1=ON
DSP_NIGHTMODE = 0x18   # 0=OFF 1=ON
DSP_HIGHPASS  = 0x1A   # 0=OFF 1=60Hz 2=80Hz 3=100Hz 4=120Hz  (PRO2 only)
DSP_OUTPUT    = 0x1C   # 0=Stereo 1=Mono 2=Left 3=Right
DSP_TUNING    = 0x1D   # 0=13L Enclosure 1=Open Back  (PRO2 only)
DSP_BALANCE   = 0x1E   # signed byte -6..+6  (UNCONFIRMED — needs sniffer)

EQ_PRESETS  = ["Normal", "Acoustic", "Jazz", "Pop", "Hip-Hop"]
HP_OPTIONS  = ["OFF", "60Hz", "80Hz", "100Hz", "120Hz"]
OUT_OPTIONS = ["Stereo", "Mono", "Left", "Right"]

# ── Per-product chime counts ────────────────────────────────────────────────
PRODUCT_CHIMES = {
    PRODUCT_PRO2:  15,
    PRODUCT_V3:    15,
    PRODUCT_IO1:   10,
    PRODUCT_V2:    10,
    PRODUCT_PRO:   6,
    PRODUCT_MICRO: 0,
}

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
