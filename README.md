# Lithe Audio — Home Assistant Integration

Direct, local control of Lithe Audio Wi-Fi speakers over the LUCI protocol on port 7777. No cloud, no bridge process, no portal — Home Assistant talks to each speaker directly.

**Latest: 1.1.12** — protocol framing finalized against real device captures; album art, MAC, Wi-Fi band, chimes, EQ, position tracking all working on WiFi PRO 2 (CR443GP_3713).

---

## Supported speakers

| Product | Platform | TLS | Chimes | EQ/DSP | Loudness | Bluetooth |
|---|---|---|---|---|---|---|
| WiFi PRO 2 | LS10 | ✅ | 15 | Full (EQ, Output, HPF, Tuning, Balance) | Slider −10..+10 dB | ✅ |
| WiFi Speaker V3 | LS10 | ✅ | 6 | EQ, Output, Balance | On/Off | ✅ |
| iO1 | LS10 | ✅ | 10 | EQ, Output, Balance | On/Off | ✅ |
| WiFi Speaker V2 | LS9 | — | 0 | EQ, Output, Balance | On/Off | ✅ |
| WiFi PRO | LS9 | — | 6 | EQ, Output, Balance | On/Off | ✅ |
| Micro Subwoofer | LS9 | — | 0 | — | — | ✅ |

LS10 speakers use a bundled developer cert automatically. LS9 speakers connect plain-TCP.

---

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/litheaudio/ha-lithe-audio` as category **Integration**.
3. Search for **Lithe Audio**, install, restart Home Assistant.
4. **Settings → Devices & Services → + Add Integration → Lithe Audio**.

### Manual

Copy `custom_components/lithe_audio/` into your HA `config/custom_components/` directory and restart.

> **No certificates required.** The Lithe-issued client cert (`client.pem` + `client.key`) is bundled with the integration in `custom_components/lithe_audio/certs/`. LS10 speakers use it automatically; LS9 speakers don't need one.

### Adding speakers

1. **Settings → Devices & Services → + Add Integration → Lithe Audio**.
2. Choose **Scan network for speakers** (uses LSSDP on UDP 1800) or **Enter speaker IP manually**.
3. Pick the discovered speaker, or enter its IP and model. That's it — no cert paste, no file paths.

If discovery doesn't find anything, your HA may be on a different VLAN/subnet or not in host-network mode. Use manual entry with the speaker's IP (find it in your router admin, the Lithe app, or via Home Assistant's Cast integration).

---

## Entities created

Per speaker, depending on capability:

- **`media_player.<name>`** — transport, volume/mute, source list, now-playing (title, artist, album, cover art, live position), browse (favourites). `play_media` accepts direct URLs.
- **`button.<name>_chime_N`** — one per chime slot (N=1..15 for PRO2, 1..10 for iO1, 1..6 for V3 and PRO).
- **`button.<name>_reboot`** / **`button.<name>_factory_reset`** — diagnostic.
- **`select.<name>_eq_preset`** — Normal / Acoustic / Jazz / Pop / Hip-Hop.
- **`select.<name>_speaker_output`** — Stereo / Mono / Left / Right.
- **`select.<name>_high_pass_filter`** — Off / 60 / 80 / 100 / 120 Hz (PRO2 only).
- **`select.<name>_speaker_tuning`** — 13L Enclosure / Open Back (PRO2 only).
- **`number.<name>_loudness`** — slider −10..+10 dB (PRO2 only).
- **`number.<name>_balance`** — slider −6..+6.
- **`switch.<name>_night_mode`** — Night Mode toggle.
- **`switch.<name>_loudness`** — on/off (V3, iO1, V2, PRO).
- **`switch.<name>_bluetooth`** — Bluetooth on/off (all products).
- **`sensor.<name>_active_source`**, **`firmware`**, **`mac_address`**, **`wifi_band`**, **`timezone`**, **`uptime`** — diagnostic sensors.

The exact set is gated by the product's capabilities (see `PRODUCT_CAPS` in `const.py`).

---

## Services

| Service | Purpose |
|---|---|
| `lithe_audio.play_chime` | Play chime slot N |
| `lithe_audio.play_url` | Push a direct audio URL |
| `lithe_audio.play_favourite` | Play a saved favourite |
| `lithe_audio.set_name` | Rename the speaker |
| `lithe_audio.set_dsp_eq` | Set EQ preset |
| `lithe_audio.set_dsp_output` | Stereo / Mono / Left / Right |
| `lithe_audio.set_dsp_nightmode` | Toggle Night Mode |
| `lithe_audio.set_dsp_highpass` | High-pass filter frequency |
| `lithe_audio.set_dsp_balance` | −6..+6 balance |
| `lithe_audio.set_dsp_loudness` | −10..+10 dB / on-off |
| `lithe_audio.bluetooth_pair` | Enter BT pairing mode |
| `lithe_audio.bluetooth_disconnect` | Drop active BT connection |
| `lithe_audio.reboot` | Reboot the speaker (~45 s) |
| `lithe_audio.play_group` | Cast to a group UUID + sync LUCI volume on members |
| `lithe_audio.set_prayer_schedule` | Schedule daily prayer announcements via Aladhan API |
| `notify.lithe_tannoy` | PA / tannoy override: save state, pause, play URL, restore |

---

## Tannoy / PA example

```yaml
automation:
  - alias: "PA Announcement"
    trigger:
      platform: state
      entity_id: input_boolean.pa_active
      to: "on"
    action:
      service: notify.lithe_tannoy
      data:
        message: "http://192.168.1.100/announcement.mp3"
        data:
          mode: start
          volume: 80
          speakers:
            - 192.168.1.38
            - 192.168.1.17
            - 192.168.1.133

  - alias: "PA End"
    trigger:
      platform: state
      entity_id: input_boolean.pa_active
      to: "off"
    action:
      service: notify.lithe_tannoy
      data:
        message: ""
        data:
          mode: end
          speakers:
            - 192.168.1.38
            - 192.168.1.17
            - 192.168.1.133
```

---

## Prayer schedule example

```yaml
service: lithe_audio.set_prayer_schedule
data:
  city: "London"
  country: "GB"
  method: 2
  entries:
    - prayer: "fajr"
      speakers: ["192.168.1.38", "192.168.1.17"]
      url: "http://192.168.1.100/adhan.mp3"
      volume: 70
      days: "daily"
    - prayer: "dhuhr"
      speakers: ["192.168.1.38"]
      url: "http://192.168.1.100/adhan_short.mp3"
      volume: 60
      days: "friday"
```

The integration re-fetches today's prayer times daily at 00:01 local time.

---

## Cast group example

```yaml
service: lithe_audio.play_group
data:
  leader_ip: "192.168.1.38"
  uuid: "b63105f8-3da3-a238-b85f-69bb61416a71"
  url: "http://192.168.1.100/track.mp3"
  content_type: "audio/mp3"
  volume: 65
  member_ips:
    - 192.168.1.38
    - 192.168.1.17
```

For everyday group playback, also enable the built-in **Cast** integration alongside this one — it discovers the group's Chromecast endpoint automatically.

---

## Troubleshooting

### Enable debug logging

Add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.lithe_audio: debug
```

Restart HA. Every received LUCI push packet is logged as:

```
DEBUG ... RX MB#42 (554 bytes): {"CMD ID":3,"Title":"PlayView",...
```

This is invaluable for diagnosing parser mismatches if you're on a different firmware version.

### Download diagnostics

Each speaker device page has a **Download Diagnostics** option. It produces a redacted JSON snapshot of the speaker's full state — useful when reporting bugs.

### Common issues

- **"Cannot connect" on first add** — make sure port 7777 is reachable from HA. Run `nc -vz <speaker-ip> 7777` from the HA terminal. If refused, power-cycle the speaker and confirm the Lithe app can talk to it before retrying.
- **Entities exist but everything is "Unknown"** — the registration handshake completed but state pushes aren't flowing. Almost always a protocol mismatch on older firmware. Enable debug logging and check the log for `RX MB#X` lines.
- **Speaker IP changed** — delete the entry in Settings → Devices & Services and re-add with the new IP, or set a DHCP reservation in your router.

---

## Protocol notes

- **Port 7777**, persistent TCP (LS9) or TLS 1.2 (LS10).
- LS10 = TLS 1.2 only (**not** TLS 1.3). Server cert is self-signed against the same CA as the client cert, so `verify_mode = CERT_NONE` is required; mutual auth is still enforced via the client cert.
- Registration payload **must** use the literal key `APP_info` with capital `APP_` — lowercase silently fails and the speaker stops pushing state.
- LS9 registration is a plain IP string, no JSON wrapper.
- Header is 10 bytes: `RemoteID(2) CmdType(1) MBID(2) Status(1) CRC(2) DataLen(2)`.
- TX is little-endian; `DataLen` is the payload length only, and a separate `0x00` NUL terminator is appended after the payload.
- RX is big-endian; `DataLen` on incoming packets **includes** the trailing NUL byte. Total bytes per RX packet = `10 + DataLen`.
- The speaker never expects a response to MB#10 (HOST MCU Playback Auth); never respond to it — sending MB#11 from a network client stops playback.
- After registration, wait ~400 ms before sending the first command.
- DSP commands tunnel through MB#112 with a 6-byte sub-packet: `0x00 0x04 <subMB hi> <subMB lo> 0x02 <value>`.

See `lithe_ha_integration_spec.md` for the full protocol reference.

---

## Changelog

### 1.1.12
- **FIX** Album art now works — added `CoverArtUrl` as the primary artwork key (what Lithe firmware CR443GP_3713 actually sends; was missing).
- **FIX** MB#42 parser now requires the `Window CONTENTS` wrapper and ignores the outer `Title` field, which is just the view name (`"PlayView"`, `"BrowseView"`).
- **FIX** MAC address now correctly parsed from MB#91. Real format is `<Interface>:<MAC>` e.g. `Wlan0:CC:90:93:10:2E:8C`. Wi-Fi MAC takes precedence over Ethernet.
- **FIX** Wi-Fi Band sensor now populated when the MB#91 Wlan0 entry is seen.
- All fixes verified against real packet captures from a WiFi PRO 2 running CR443GP_3713.

### 1.1.11
- **FIX** RX framing per official LUCI Tech Note v15.0.7 §5.2.3 — TX and RX framing are asymmetric. Clients send LE without including the NUL in DataLen, but receive BE with NUL counted inside DataLen. v1.1.10's `+1` on RX was wrong and caused reconnect loops.

### 1.1.10
- **FIX** TX `DataLen` correctly excludes the NUL terminator (was including it). Verified against four documented spec examples: PLAY (dlen=4), `play 3` (dlen=6), FAV_SAVE:1 (dlen=10), GET MB#42 (dlen=0).
- **NEW** MB#42 title parser tries multiple candidate keys; falls back to artist when title is empty (Spotify Connect quirk).

### 1.1.9
- **NEW** MB#42 parser handles many wrapper / artwork / duration key conventions across LinkPlay firmware variants.
- **NEW** Relative artwork paths resolved against speaker IP.
- **NEW** Duration auto-detects ms vs seconds.
- **NEW** MB#208 / MB#91 parsers case- and separator-insensitive.

### 1.1.8
- **NEW** Live ticking position via `media_position_updated_at`.
- **NEW** Debug logging records every received push packet.

### 1.1.7
- **FIX** LUCI packet header is 10 bytes, not 9 — CRC is 2 bytes not 1. Every TX packet was one byte short; speakers silently dropped commands.

### 1.1.6
- **FIX** `load_cert_chain` moved off the event loop (executor).

### 1.1.5
- **FIX** Reverted TLS to `CERT_NONE` (proven-working from v1.0). v1.1.4's `CERT_REQUIRED` broke LS10 due to self-signed-chain rejection.

### 1.1.4
- **FIX** TLS context build order — `check_hostname = False` before `verify_mode = CERT_NONE`.

### 1.1.3
- **FIX** Legacy config entries without `product` key no longer crash — inferred from entry title and migrated in place.
- **FIX** TLS products without a stored cert path fall back to the bundled cert.
- **NEW** Downloadable diagnostics.

### 1.1.2
- **FIX** Chime counts corrected: V3 = 6 (was 15), V2 = 0 (was 10).
- **FIX** Bluetooth, EQ, Speaker Output, Balance, Loudness switch, Night Mode opened up to PRO and V2 (were LS10-only).
- **REFACTOR** Single `PRODUCT_CAPS` dict drives every entity gate.

### 1.1.1
- **NEW** LSSDP network discovery on UDP port 1800.
- **NEW** Client certificate bundled with the integration.
- **CHANGED** Config flow: Scan vs Manual as the first step.
- **REMOVED** cert_path / key_path from the config flow — users never touch certificates.

### 1.1.0
- **NEW** Button platform — chime buttons, Reboot, Factory Reset.
- **NEW** Tannoy / PA override (`notify.lithe_tannoy`).
- **NEW** Prayer scheduler (`lithe_audio.set_prayer_schedule`).
- **NEW** Cast-group casting (`lithe_audio.play_group`).
- **NEW** `media_player.play_media` accepts direct URLs.
- **NEW** `media_player.browse_media` exposes favourites.
- **NEW** `play_url`, `play_favourite`, `set_name` services.
- **FIX** SOURCES table corrected to match LUCI API v15.0.7.
- **FIX** Reboot now uses MB#114 (was incorrectly MB#37).
- **FIX** PLAY_STATES map now handles 4 = receiving and 5 = buffering.
- **FIX** `remove_callback` no longer raises on list.
- **FIX** `select_source` now sends MB#50 SET instead of being a stub.
- **FIX** SEEK feature flag is now dynamic — removed for live streams.

### 1.0.0
- Initial release: media_player, select, number, switch, sensor entities; DSP control; chime/Bluetooth/reboot services.

---

## License

MIT — see `LICENSE`.

## Contributing

PRs welcome. Please include a debug-log snippet showing the relevant `RX MB#…` packets when reporting protocol-layer bugs.
