# Lithe Audio for Home Assistant

The official Home Assistant integration for [Lithe Audio](https://litheaudio.com)
speakers. Control playback, volume, sources, chimes, and presets directly from
Home Assistant — local-only, no cloud, no polling delays. State updates appear in
Home Assistant instantly because the speaker pushes them.

## Features

- **Playback** — play, pause, stop, next, previous, seek
- **Volume & mute** — full 0-100% control with smooth slider response
- **Now-playing metadata** — title, artist, album, album art
- **Source detection** — Spotify, AirPlay, Tidal, Cast, Bluetooth, AUX, and more
- **Built-in chimes** — doorbell, alarm, notification tones triggered as Home
  Assistant buttons or via the `play_chime` service
- **Favourites / presets** — save and recall playback by slot (1-9)
- **AUX line-in and Bluetooth** — switch inputs from automations
- **Multi-room** — works alongside Home Assistant's media player group helper
  and Google Cast groups for synchronised playback
- **Auto-discovery** — speakers appear in Home Assistant automatically when
  detected on the local network
- **Secure** — encrypted connection (required by certain models) is fully
  handled by the integration

## Supported speakers

All current Lithe Audio network speakers, including:

- PRO2 (in-ceiling)
- WiFi V3 / iO1
- WiFi V2 / PRO
- Micro Subwoofer

The integration handles encrypted connections transparently for speaker
models that require them — no extra setup steps for the installer.

## Installation

### Via HACS (recommended)

1. In HACS go to *Integrations*, click the three-dot menu → *Custom repositories*
2. Paste this repository's URL, category *Integration*
3. Search for **Lithe Audio** in HACS and install
4. Restart Home Assistant
5. *Settings → Devices & services → Add Integration → Lithe Audio*

### Manual install

Copy the `custom_components/lithe_audio/` folder into your Home Assistant
`<config>/custom_components/` directory and restart Home Assistant.

## Setup

1. Go to *Settings → Devices & services → Add Integration → Lithe Audio*
2. Choose **Scan network** to find speakers automatically, or **Enter IP
   manually** if you prefer
3. Confirm — done

Entities appear instantly. The integration tests the connection before
saving, so any issue is reported immediately.

## Entities

Each speaker creates one device with these entities:

| Entity                   | What it does                                          |
| ------------------------ | ----------------------------------------------------- |
| `media_player.*`         | Main control — play, pause, volume, source, metadata  |
| `switch.mute`            | Mute toggle for automations                           |
| `switch.line_in`         | Enable / disable AUX or line-in input                 |
| `switch.bluetooth`       | Enable Bluetooth receiver mode                         |
| `sensor.source`          | Current audio source (Spotify, AirPlay, etc.)         |
| `sensor.now_playing`     | "Artist — Title" for easy automation triggers          |
| `sensor.firmware`        | Speaker firmware version (diagnostic)                 |
| `button.chime_1..15`     | One button per built-in chime (count varies by model) |
| `button.preset_1..9`     | One-tap recall of saved favourites                    |
| `button.reboot`          | Restart the speaker                                   |
| `number.volume`          | Alternate volume slider (disabled by default)         |

## Services

| Service                          | Use case                                            |
| -------------------------------- | --------------------------------------------------- |
| `lithe_audio.play_chime`         | Doorbell, alarm, scheduled announcement              |
| `lithe_audio.play_preset`        | One-tap radio / playlist recall                     |
| `lithe_audio.save_preset`        | Capture current playback into a slot                |
| `lithe_audio.delete_preset`      | Clear a slot                                        |
| `lithe_audio.play_direct`        | Stream a URL or play a built-in file                 |
| `lithe_audio.send_raw_command`   | Advanced control (see Advanced section below)        |
| `lithe_audio.reboot`             | Restart the speaker                                 |

### Example — doorbell automation

```yaml
automation:
  - alias: "Front door chime"
    trigger:
      - platform: state
        entity_id: binary_sensor.front_door_button
        to: "on"
    action:
      - service: lithe_audio.play_chime
        target:
          entity_id:
            - media_player.living_room
            - media_player.kitchen
        data:
          chime_index: 1
```

### Example — morning radio

```yaml
script:
  morning_radio:
    sequence:
      - service: lithe_audio.play_preset
        target:
          entity_id: media_player.kitchen
        data:
          preset_slot: 1
      - service: media_player.volume_set
        target:
          entity_id: media_player.kitchen
        data:
          volume_level: 0.35
```

## Multi-room

Two options work well alongside this integration:

- **Google Cast groups** — create a group in the Google Home app (e.g. "Whole
  House"); it appears as a Cast target that Home Assistant's built-in Cast
  integration handles. Synchronised audio across all speakers in the group.
- **Media player group helper** — Home Assistant's built-in helper that bundles
  several Lithe entities into one virtual player for simultaneous transport
  commands. Volume and play/pause apply to all members at once (no audio
  synchronisation, but useful for scenes and automations).

## Troubleshooting

**Speaker shows as unavailable**
- Verify the speaker is on the same network as Home Assistant
- Try removing and re-adding the integration; the IP address may have changed
- Try restarting the speaker

**Enable debug logging**

Add to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.lithe_audio: debug
```

Restart Home Assistant and check *Settings → System → Logs* for detailed output.

## Advanced

### Raw command service

The `send_raw_command` service is an escape hatch for power users who want to
trigger features not exposed as entities. It sends a low-level command directly
to the speaker. Lithe Audio integration partners can request the full command
reference from **developer@litheaudio.com**.

### Diagnostics

From the integration's device page in Home Assistant, click *Download
diagnostics* to get a sanitised state dump suitable for attaching to GitHub
issues. Certificates, MAC addresses, and serial numbers are automatically
redacted.

## Support

- **Bugs and feature requests:** open an issue on
  [GitHub](https://github.com/LitheAudio-Official/home-assistant-integration/issues)
- **Hardware support:** developer@litheaudio.com

## Licence

MIT — see [LICENSE](LICENSE).
