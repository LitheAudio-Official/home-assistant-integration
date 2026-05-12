# Lithe Audio — Home Assistant Integration

Direct, local control of Lithe Audio WiFi speakers over the LUCI protocol on port 7777. No cloud, no bridge process, no portal — Home Assistant talks to each speaker directly.

**Version 1.1.0** — adds buttons, browse, tannoy/PA, prayer scheduler, Cast groups.

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

LS10 speakers use the bundled developer cert automatically. LS9 speakers connect plain-TCP.

---

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/litheaudio/ha-lithe-audio` as category **Integration**
3. Search for **Lithe Audio**, install, restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Lithe Audio**.

### Manual

Copy `custom_components/lithe_audio/` into your HA `config/custom_components/` and restart.

> **No certificates required.** The Lithe-issued client cert (`client.pem` + `client.key`) is bundled with the integration in `custom_components/lithe_audio/certs/`. LS10 speakers use it automatically; LS9 speakers don't need one.

### Adding speakers

1. **Settings → Devices & Services → Add Integration → Lithe Audio**.
2. Choose **Scan network for speakers** (uses LSSDP on UDP 1800) or **Enter speaker IP manually**.
3. Pick the discovered speaker, or enter its IP and model. That's it — no cert paste, no file paths.

---

## Changelog

### 1.1.4
- **FIX** TLS context build order — `check_hostname` is now disabled before `verify_mode` is set, fixing `Cannot set verify_mode to CERT_NONE when check_hostname is enabled` on LS10 connect.
- **CHANGED** When a client cert is loaded, it's now also used as the CA trust anchor with `verify_mode = CERT_REQUIRED` (proper mutual TLS). Falls back to `CERT_NONE` only if loading the CA fails.

### 1.1.3
- **FIX** Config entries created by older versions without the `product` key no longer crash on setup — the integration now infers the product from the entry title and migrates the data in place.
- **FIX** TLS products without a stored cert path now fall back to the bundled cert automatically.
- **NEW** Downloadable diagnostics (`diagnostics.py`). Click the speaker device → Download Diagnostics for a redacted state dump. Also silences the spurious `blocking call to import_module ... diagnostics` warning from HA.

### 1.1.2
- **FIX** Chime counts corrected: V3 = 6 (was 15), V2 = 0 (was 10).
- **FIX** Bluetooth opened up to all products including iO1 and Micro Sub (was PRO2/V3 only).
- **FIX** EQ Preset, Speaker Output, Balance, Loudness switch and Night Mode now available on PRO and V2 too (was LS10-only).
- **REFACTOR** Single `PRODUCT_CAPS` dict in `const.py` now drives every entity gate — no more scattered `if product in (…)` checks across platform files.
- **NOTE** Micro Sub DSP (low-pass filter, mode, phase, crossover, gain) intentionally not yet implemented — pending sub-MB ID capture.

### 1.1.1
- **NEW** LSSDP network discovery on UDP port 1800 — restored "Scan network" path.
- **NEW** Client certificate now bundled with the integration (`certs/client.pem`, `certs/client.key`); LS10 speakers use it automatically.
- **CHANGED** Config flow now offers Scan vs Manual as the first step (matches the original v1.0 design).
- **REMOVED** cert_path / key_path fields from the config flow — users never need to touch certificates.

---

## Entities created

Per speaker, depending on capability:

- **`media_player.<name>`** — transport, volume/mute, source list, now-playing, browse (favourites), `play_media` accepts direct URLs.
- **`button.<name>_chime_N`** — one per chime slot (N=1..15 for PRO2/V3, 1..10 for iO1/V2, 1..6 for PRO).
- **`button.<name>_reboot`**, **`button.<name>_factory_reset`** — diagnostic.
- **`select.<name>_eq_preset`** — Normal/Acoustic/Jazz/Pop/Hip-Hop (LS10 only).
- **`select.<name>_speaker_output`** — Stereo/Mono/Left/Right (LS10 only).
- **`select.<name>_high_pass_filter`** — Off/60/80/100/120 Hz (PRO2 only).
- **`select.<name>_speaker_tuning`** — 13L Enclosure / Open Back (PRO2 only).
- **`number.<name>_loudness`** — slider -10..+10 dB (PRO2 only).
- **`number.<name>_balance`** — slider -6..+6 (LS10 only).
- **`switch.<name>_night_mode`** — (LS10 only).
- **`switch.<name>_loudness`** — on/off for V3/iO1.
- **`switch.<name>_bluetooth`** — on/off for PRO2/V3.
- **`sensor.<name>_active_source`**, **`firmware`**, **`mac_address`**, **`wifi_band`**, **`timezone`**, **`uptime`** — diagnostics.

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
| `lithe_audio.set_dsp_highpass` | HPF frequency |
| `lithe_audio.set_dsp_balance` | -6..+6 balance |
| `lithe_audio.set_dsp_loudness` | -10..+10 dB / on-off |
| `lithe_audio.bluetooth_pair` | Enter BT pairing mode |
| `lithe_audio.bluetooth_disconnect` | Drop active BT |
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
  leader_ip:   "192.168.1.38"
  uuid:        "b63105f8-3da3-a238-b85f-69bb61416a71"
  url:         "http://192.168.1.100/track.mp3"
  content_type: "audio/mp3"
  volume:      65
  member_ips:
    - 192.168.1.38
    - 192.168.1.17
```

For everyday group playback, also enable the built-in **Cast** integration alongside this one — it discovers the group's Chromecast endpoint automatically.

---

## Protocol notes

- LS10 = TLS 1.2 only (not 1.3). Registration payload **must** use the literal key `APP_info` with capital `APP_` — lowercase silently fails and the speaker stops pushing state.
- LS9 registration is a plain IP string, no JSON wrapper.
- TX uses little-endian for MBID and DataLen; RX is big-endian.
- Never respond to MB#10 (HOST MCU Playback Auth) — sending MB#11 from a network client stops playback.
- After registration, wait ~400 ms before sending the first command.
- DSP commands tunnel through MB#112 with a 6-byte sub-packet.

See `lithe_ha_integration_spec.md` for the full protocol reference.

---

### 1.1.0
- **NEW** Button platform — chime buttons (one per slot, per-product gated), Reboot, Factory Reset.
- **NEW** Tannoy / PA override (`notify.lithe_tannoy`).
- **NEW** Prayer scheduler (`lithe_audio.set_prayer_schedule`).
- **NEW** Cast-group casting (`lithe_audio.play_group`).
- **NEW** `media_player.play_media` accepts direct URLs.
- **NEW** `media_player.browse_media` exposes favourites.
- **NEW** `play_url`, `play_favourite`, `set_name` services.
- **FIX** SOURCES table corrected to match LUCI API v15.0.7.
- **FIX** Reboot now uses MB#114 (was incorrectly MB#37).
- **FIX** PLAY_STATES map now handles 4=receiving and 5=buffering.
- **FIX** `remove_callback` no longer raises on list.
- **FIX** `select_source` now sends MB#50 SET instead of being a stub.
- **FIX** SEEK feature flag is now dynamic — removed for live streams.

### 1.0.0
- Initial release: media_player, select, number, switch, sensor entities; DSP control; chime/Bluetooth/reboot services.
