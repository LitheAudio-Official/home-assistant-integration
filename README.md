# Lithe Audio for Home Assistant

A full-featured Home Assistant integration for [Lithe Audio](https://litheaudio.com)
speakers using the **LUCI** local protocol — no cloud, no polling, no proprietary apps
required. Push-based state updates, automatic reconnect, and complete feature parity
with the Lithe Audio companion app.

## Features

| Capability                  | Implementation                                  |
| --------------------------- | ----------------------------------------------- |
| Play / Pause / Stop / Seek  | MB#40 (transport)                               |
| Next / Previous             | MB#40 NEXT / PREV                               |
| Volume 0–100 + step / mute  | MB#64 / MB#63 (with 250ms debounce)             |
| Source detection            | MB#50 → mapped to human-readable names           |
| Now-playing metadata        | MB#42 JSON push (title/artist/album/art)        |
| Embedded chimes (1–15)      | MB#80 — exposed as buttons + `play_chime` svc    |
| Favourites / Presets        | MB#70 FAV_SAVE / FAV_PLAY / FAV_DELETE          |
| AUX / Line-in switching     | MB#95 / MB#96                                   |
| Bluetooth power             | MB#209 ON / OFF                                 |
| Reboot                      | MB#37                                           |
| Raw command escape hatch    | Any MBID via `send_raw_command` service          |
| Multi-room grouping         | Via HA's native `media_player.join` (Cast)      |
| LSSDP auto-discovery        | Port 1800 multicast (Lithe-specific)            |
| Zeroconf discovery          | `_googlecast._tcp.local.` with `md=Lithe*`      |
| LS10 TLS 1.2 mutual auth    | Client certificate required (see below)         |
| LS9 plain TCP               | No certificate                                   |

### Supported models

| Platform | Models                                  | Connection         |
| -------- | --------------------------------------- | ------------------ |
| **LS10** | PRO2, WiFi V3, iO1                      | TLS 1.2 + cert     |
| **LS9**  | WiFi V2, PRO, Micro Subwoofer           | Plain TCP          |

## Installation

### Via HACS (recommended)

1. In HACS → *Integrations*, click the three-dot menu → *Custom repositories*.
2. Add this repo URL with category *Integration*.
3. Search for **Lithe Audio** and install.
4. Restart Home Assistant.

### Manual

Copy `custom_components/lithe_audio/` to your `<config>/custom_components/` directory
and restart Home Assistant.

## Setup

1. *Settings → Devices & services → Add Integration → **Lithe Audio***.
2. Choose **Scan network** (LSSDP) to auto-discover speakers, or enter an IP manually.
3. For **LS10** speakers (PRO2, V3, iO1) you'll be prompted to paste the contents of
   `client.pem` and `client.key`. These are issued per-developer by Lithe Audio —
   contact **developer@litheaudio.com** to request them.
4. The integration will test the TLS handshake before saving anything. If it fails,
   you'll get a clear error rather than a silent connection problem later.

### LS10 certificate notes

LS10 speakers use TLS 1.2 mutual authentication. The Lithe-issued `client.pem` is used
as both the client certificate **and** the trust anchor (CA), because Lithe signs each
speaker's own server certificate with the same CA. Hostname verification is disabled
because the speaker's cert isn't bound to a specific IP.

Certificates are stored in the config entry data; they never leave your Home Assistant
instance.

## Entities

Each speaker creates one device with several entities:

- **media_player** — primary control surface (play, pause, volume, source, metadata, art)
- **switch.mute** — binary mute toggle for automations
- **switch.line_in** / **switch.bluetooth** — input enable (disabled by default)
- **sensor.source** / **sensor.now_playing** / **sensor.firmware** — diagnostics
- **button.chime_1**…**chime_15** — embedded cue triggers (count varies by model)
- **button.preset_1**…**preset_9** — recall saved favourites
- **button.reboot** — restart the speaker
- **number.volume** — alternate volume control (disabled by default)

## Services

| Service                          | Use case                                       |
| -------------------------------- | ---------------------------------------------- |
| `lithe_audio.play_chime`         | Doorbell / alarm / scheduled announcement      |
| `lithe_audio.play_preset`        | One-tap radio station / playlist               |
| `lithe_audio.save_preset`        | Capture current playback into a slot           |
| `lithe_audio.delete_preset`      | Clear a slot                                   |
| `lithe_audio.play_direct`        | Stream a URL, or play `/system/usr/songN.mp3`  |
| `lithe_audio.send_raw_command`   | Diagnostics / advanced MBID experiments         |
| `lithe_audio.reboot`             | Recover from a wedged speaker                  |

### Example: doorbell automation

```yaml
automation:
  - alias: "Doorbell chime"
    trigger:
      platform: state
      entity_id: binary_sensor.front_door_button
      to: "on"
    action:
      service: lithe_audio.play_chime
      target:
        entity_id:
          - media_player.living_room_lithe
          - media_player.kitchen_lithe
      data:
        chime_index: 1
```

### Example: play a saved Tidal station

```yaml
script:
  morning_radio:
    sequence:
      - service: lithe_audio.play_preset
        target:
          entity_id: media_player.kitchen_lithe
        data:
          preset_slot: 1
      - service: media_player.volume_set
        target:
          entity_id: media_player.kitchen_lithe
        data:
          volume_level: 0.35
```

## Multi-room grouping

Lithe LS10 speakers use **Google Cast groups** for synchronised multi-room playback —
create the group once in the Google Home app and it appears as a separate Cast target
that Home Assistant's built-in Cast integration handles automatically. This integration
focuses on per-speaker control; for synchronised whole-home audio, use a Cast group as
the playback target and use this integration to control individual zones (volume,
mute, chimes per room, etc.).

You can also use the **media_player group** helper in Home Assistant to bundle several
Lithe entities into one virtual player for simultaneous transport commands without
audio synchronisation.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Home Assistant                                     │
│                                                     │
│  ┌─────────────┐   ┌──────────────────────────┐    │
│  │ media_player│   │  LitheAudioCoordinator   │    │
│  │ button      │◄──┤  (push-driven, no poll)  │    │
│  │ switch      │   └────────────┬─────────────┘    │
│  │ sensor      │                │ DeviceState       │
│  │ number      │                │ listeners         │
│  └─────────────┘                │                   │
│                                 ▼                   │
│                  ┌──────────────────────────┐      │
│                  │   LitheAudioClient       │      │
│                  │   (async LUCI protocol)  │      │
│                  └──────────┬───────────────┘      │
└─────────────────────────────┼──────────────────────┘
                              │ TCP 7777
                              │ (TLS 1.2 if LS10)
                              ▼
                  ┌──────────────────────────┐
                  │  Lithe Audio Speaker     │
                  │  (LUCI v15.x firmware)   │
                  └──────────────────────────┘
```

The client maintains one persistent socket per speaker. On connect it sends `MB#3`
(register), then `GET MB#50/51/42/64/63` for the initial state sync, then sits in a
read loop dispatching pushed state updates. On disconnect it reconnects with
exponential backoff (2s → 60s) and re-registers automatically.

## Troubleshooting

**Speaker appears unavailable**
- Check the speaker is on the same subnet as Home Assistant
- Verify port 7777 is reachable: `nc -zv <speaker-ip> 7777`
- For LS10, double-check the certificate hasn't been re-formatted (line breaks matter)

**Push updates stop arriving**
- The integration sends a `GET MB#51` keepalive every ~50s; if pushes are lost,
  enable debug logging:
  ```yaml
  logger:
    logs:
      custom_components.lithe_audio: debug
  ```

**LS10 handshake fails with `SSL: TLSV1_ALERT_*`**
- The cert isn't signed by Lithe's CA, or the speaker firmware predates the
  TLS rollout. Contact Lithe Audio support.

## Credits

- Built against the **LUCI v15.x** protocol as documented in:
  - *Lithe Audio LUCI Integration API — Partner Developer Guide*
  - *Libre Wireless Technologies LUCI Technical Note v15.0.7*

## Licence

MIT — see [LICENSE](LICENSE).
