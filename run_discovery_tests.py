"""Self-test for the LSSDP discovery parser.

Validates ``_parse_response`` against the exact example response packet
documented in the Libre Wireless LUCI Technical Note §5.2.1.
"""
import importlib.util
import sys
import types
from pathlib import Path

# Same package-injection trick as run_protocol_tests.py
sys.modules["lithe_audio_test_pkg"] = types.ModuleType("lithe_audio_test_pkg")

_base = Path(__file__).parent / "custom_components" / "lithe_audio"
for name in ("const", "discovery"):
    spec = importlib.util.spec_from_file_location(
        f"lithe_audio_test_pkg.{name}", _base / f"{name}.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"lithe_audio_test_pkg.{name}"] = mod
    spec.loader.exec_module(mod)

discovery = sys.modules["lithe_audio_test_pkg.discovery"]
const = sys.modules["lithe_audio_test_pkg.const"]


# Documented M-SEARCH response (Tivoli M2D example from the Tech Note)
SAMPLE = (
    b"HTTP/1.1 200 OK\r\n"
    b"USN:cc90932be79b\r\n"
    b"HOST:239.255.255.250:1800\r\n"
    b"Version:LSSDP 1.0\r\n"
    b"FN:0\r\n"
    b"FWVERSION:eng.C4A.2273.109.1\r\n"
    b"CAST_FWVERSION:1.56.eng.C4A.2273\r\n"
    b"CAST_TIMEZONE:\r\n"
    b"CAST_MODEL:Tivoli_M2D\r\n"
    b"PORT:7777\r\n"
    b"DeviceName:M2D Garage speaker White\r\n"
    b"State:S\r\n"
    b"NETMODE:WLAN\r\n"
    b"SPEAKERTYPE:Wireless Speaker\r\n"
    b"TCPPORT:2020\r\n"
    b"WIFIBAND:2G\r\n"
    b"SOURCE_LIST:LS10::f7ffffff\r\n"
    b"MRAMode:DDMS\r\n"
)

# Synthetic LS9 (Lithe PRO) response
SAMPLE_LS9 = (
    b"HTTP/1.1 200 OK\r\n"
    b"USN:aabbccddeeff\r\n"
    b"Version:LSSDP 1.0\r\n"
    b"PORT:7777\r\n"
    b"DeviceName:Lithe PRO Kitchen\r\n"
    b"CAST_MODEL:Lithe_PRO\r\n"
    b"SOURCE_LIST:LS9::abcd1234\r\n"
)

# Non-LSSDP traffic (should be ignored)
SAMPLE_BOGUS = b"HTTP/1.1 200 OK\r\nServer:nginx\r\n\r\n"


fails = 0
def check(name, cond):
    global fails
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        fails += 1


print("\n=== LSSDP parser ===\n")

dev = discovery._parse_response(SAMPLE, "192.168.1.50")
check("Parsed LS10 device", dev is not None)
check("LS10 host preserved", dev.host == "192.168.1.50")
check("LS10 port from header", dev.port == 7777)
check("LS10 name from DeviceName", dev.name == "M2D Garage speaker White")
check("LS10 model from CAST_MODEL", dev.model == "Tivoli_M2D")
check("LS10 firmware version", dev.firmware == "eng.C4A.2273.109.1")
check("LS10 MAC formatted as colons",
      dev.mac == "CC:90:93:2B:E7:9B")
check("LS10 platform detected from SOURCE_LIST",
      dev.platform == const.PLATFORM_LS10)
check("LS10 unique_id stable",
      dev.unique_id == "cc90932be79b")
check("LS10 raw_headers populated",
      dev.raw_headers and dev.raw_headers["USN"] == "cc90932be79b")

dev_ls9 = discovery._parse_response(SAMPLE_LS9, "192.168.1.42")
check("Parsed LS9 device", dev_ls9 is not None)
check("LS9 platform detected from SOURCE_LIST",
      dev_ls9.platform == const.PLATFORM_LS9)
check("LS9 MAC", dev_ls9.mac == "AA:BB:CC:DD:EE:FF")

bogus = discovery._parse_response(SAMPLE_BOGUS, "192.168.1.99")
check("Non-LSSDP traffic ignored", bogus is None)

print(f"\n  {fails} failure(s)\n")
sys.exit(fails)
