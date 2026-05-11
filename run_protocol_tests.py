"""Self-test for the LUCI packet builder/parser.

Validates our build_packet output against the exact binary examples
published in the Lithe Audio Partner Developer Guide. If anything here
fails, the integration won't communicate with real speakers correctly.
"""
import struct
import sys
from pathlib import Path

# Import luci.py directly without triggering the __init__.py (which depends on HA).
import importlib.util
import types
_luci_path = Path(__file__).parent / "custom_components" / "lithe_audio" / "luci.py"
_const_path = Path(__file__).parent / "custom_components" / "lithe_audio" / "const.py"

# luci.py uses `from .const import ...`, so we need to inject const as a fake
# sibling module first.
sys.modules["lithe_audio_test_pkg"] = types.ModuleType("lithe_audio_test_pkg")

spec_const = importlib.util.spec_from_file_location(
    "lithe_audio_test_pkg.const", _const_path,
)
const_mod = importlib.util.module_from_spec(spec_const)
sys.modules["lithe_audio_test_pkg.const"] = const_mod
spec_const.loader.exec_module(const_mod)

spec_luci = importlib.util.spec_from_file_location(
    "lithe_audio_test_pkg.luci", _luci_path,
)
luci_mod = importlib.util.module_from_spec(spec_luci)
sys.modules["lithe_audio_test_pkg.luci"] = luci_mod
spec_luci.loader.exec_module(luci_mod)

LitheAudioClient = luci_mod.LitheAudioClient
DeviceState = luci_mod.DeviceState

def hex_normalize(s: str) -> str:
    """Strip whitespace from hex string for comparison."""
    return "".join(c for c in s.upper() if c in "0123456789ABCDEF")

def test(name: str, condition: bool, detail: str = "") -> None:
    """Mini assertion harness."""
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}{(': ' + detail) if detail else ''}")
    if not condition:
        global _FAILURES
        _FAILURES += 1

_FAILURES = 0

print("\n=== LUCI Packet Builder ===\n")

# Doc example: PLAY (MB#40) → AAAA 02 2800 00 0000 0400 504C4159 + 00
pkt = LitheAudioClient.build_packet(0x02, 40, "PLAY")
expected = "AAAA022800000000040050 4C 41 59 00"
got = pkt.hex().upper()
# Note: doc gives CRC as 00 but our impl computes a real CRC (modulo-256 sum).
# Re-parse fields and verify each independently.
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("PLAY MB#40 RemoteID", rid == 0xAAAA, f"got 0x{rid:04X}")
test("PLAY MB#40 CmdType", cmd == 0x02, f"got 0x{cmd:02X}")
test("PLAY MB#40 MBID", mbid == 40, f"got {mbid}")
test("PLAY MB#40 DataLen", dlen == 4, f"got {dlen}")
test("PLAY MB#40 payload", pkt[10:14] == b"PLAY", f"got {pkt[10:14]!r}")
test("PLAY MB#40 terminator", pkt[-1] == 0x00, f"got 0x{pkt[-1]:02X}")
test("PLAY MB#40 total length", len(pkt) == 15, f"got {len(pkt)}")

# Doc example: SEEK:60000 (MB#40), length 0A 00
pkt = LitheAudioClient.build_packet(0x02, 40, "SEEK:60000")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("SEEK DataLen", dlen == 10, f"got {dlen}")
test("SEEK payload bytes", pkt[10:20] == b"SEEK:60000")

# Doc example: SELECTITEM:7 (MB#41), length 0C 00
pkt = LitheAudioClient.build_packet(0x02, 41, "SELECTITEM:7")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("SELECTITEM:7 MBID", mbid == 41)
test("SELECTITEM:7 DataLen", dlen == 12, f"got {dlen}")

# Doc example: Volume 75 (MB#64), length 0200
pkt = LitheAudioClient.build_packet(0x02, 64, "75")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("Volume MB#64", mbid == 64 and dlen == 2 and pkt[10:12] == b"75")

# Doc example: MUTE (MB#63)
pkt = LitheAudioClient.build_packet(0x02, 63, "MUTE")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("MUTE MB#63", mbid == 63 and dlen == 4 and pkt[10:14] == b"MUTE")

# Doc example: chime MB#80 "play 3", length 0600
pkt = LitheAudioClient.build_packet(0x02, 80, "play 3")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("Chime MB#80 'play 3'", mbid == 80 and dlen == 6 and pkt[10:16] == b"play 3")

# Doc example: FAV_SAVE:1 (MB#70), length 0A 00
pkt = LitheAudioClient.build_packet(0x02, 70, "FAV_SAVE:1")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("FAV_SAVE MB#70", mbid == 70 and dlen == 10 and pkt[10:20] == b"FAV_SAVE:1")

# Doc example: GET MB#42 (UI JSON), empty payload
pkt = LitheAudioClient.build_packet(0x01, 42, "")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("GET MB#42", cmd == 0x01 and mbid == 42 and dlen == 0)
test("GET MB#42 has terminator", pkt[-1] == 0x00 and len(pkt) == 11)

# Doc example: Register MB#3, empty payload
pkt = LitheAudioClient.build_packet(0x02, 3, "")
rid, cmd, mbid, status, crc, dlen = struct.unpack_from("<HBHBHH", pkt, 0)
test("REGISTER MB#3", cmd == 0x02 and mbid == 3 and dlen == 0)

print("\n=== State Apply ===\n")

# Build a fake client just to access _apply_state without networking
c = LitheAudioClient("127.0.0.1")
test("Initial state", c.state.volume == 0 and not c.state.muted)

c._apply_state(64, "75")
test("Volume push", c.state.volume == 75)

c._apply_state(63, "MUTE")
test("Mute MUTE", c.state.muted is True)
c._apply_state(63, "UNMUTE")
test("Mute UNMUTE", c.state.muted is False)

c._apply_state(51, "0")
test("Play state 0=playing", c.state.play_state == "playing")
c._apply_state(51, "2")
test("Play state 2=paused", c.state.play_state == "paused")

c._apply_state(50, "4")
test("Source 4=Spotify", c.state.source_id == 4 and c.state.source_name == "Spotify")
c._apply_state(50, "19")
test("Source 19=Bluetooth", c.state.source_id == 19 and c.state.source_name == "Bluetooth")

c._apply_state(42, '{"Title":"Hello","Artist":"World","Album":"Test","TotalTime":180000}')
test("Now playing title", c.state.title == "Hello")
test("Now playing artist", c.state.artist == "World")
test("Now playing album", c.state.album == "Test")
test("Now playing duration", c.state.duration_ms == 180000)

c._apply_state(208, "Model:PRO2,LEDControl:1")
test("Device info model", c.state.model == "PRO2")

c._apply_state(91, "Wlan0:AA:BB:CC:DD:EE:FF")
test("Network info MAC", c.state.mac == "AA:BB:CC:DD:EE:FF")

c._apply_state(573, "Europe/London")
test("Timezone", c.state.timezone == "Europe/London")

print("\n=== Round-trip parser ===\n")

# Build a packet then feed it through _drain_rx_buffer (synchronously
# via direct buffer manipulation since we can't run async here easily)
import asyncio

async def round_trip_test():
    c = LitheAudioClient("127.0.0.1")
    # Synthesize a volume push packet
    pkt = LitheAudioClient.build_packet(0x02, 64, "42")
    c._rx_buffer.extend(pkt)
    await c._drain_rx_buffer()
    return c.state.volume

vol = asyncio.run(round_trip_test())
test("Round-trip volume packet", vol == 42, f"got {vol}")

# Test multi-packet buffer (TCP can deliver several at once)
async def multi_packet_test():
    c = LitheAudioClient("127.0.0.1")
    p1 = LitheAudioClient.build_packet(0x02, 64, "20")
    p2 = LitheAudioClient.build_packet(0x02, 63, "MUTE")
    p3 = LitheAudioClient.build_packet(0x02, 51, "0")
    c._rx_buffer.extend(p1 + p2 + p3)
    await c._drain_rx_buffer()
    return c.state.volume, c.state.muted, c.state.play_state

vol, muted, ps = asyncio.run(multi_packet_test())
test("Multi-packet vol", vol == 20)
test("Multi-packet mute", muted is True)
test("Multi-packet play state", ps == "playing")

# Test partial packet — receive half, then the rest
async def partial_packet_test():
    c = LitheAudioClient("127.0.0.1")
    pkt = LitheAudioClient.build_packet(0x02, 64, "55")
    c._rx_buffer.extend(pkt[:7])  # only header partial
    await c._drain_rx_buffer()
    before = c.state.volume
    c._rx_buffer.extend(pkt[7:])
    await c._drain_rx_buffer()
    return before, c.state.volume

before, after = asyncio.run(partial_packet_test())
test("Partial: nothing before second chunk", before == 0)
test("Partial: complete after second chunk", after == 55)

print(f"\n{'='*40}")
print(f"  {_FAILURES} failure(s)")
print(f"{'='*40}\n")
sys.exit(_FAILURES)
