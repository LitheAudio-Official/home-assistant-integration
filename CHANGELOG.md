# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] — 2026-05-11

### Added

- Initial release
- Full local control of Lithe Audio speakers — no cloud dependency
- Push-driven state updates with automatic reconnection
- Network auto-discovery (LSSDP + zeroconf)
- One-click setup — no certificates or credentials required from the
  installer, even for models that use encrypted connections
- **Media player** with play / pause / stop / seek / volume / mute / source /
  now-playing / album art
- **Buttons** for built-in chimes (1-15 per model), preset slots 1-9, and
  speaker reboot
- **Switches** for mute, AUX line-in, and Bluetooth receiver mode
- **Sensors** for current source, now-playing string, firmware version, and
  raw play state
- **Number** entity for alternate volume control
- **Services**: `play_chime`, `play_preset`, `save_preset`, `delete_preset`,
  `play_direct`, `send_raw_command`, `reboot`
- **Brand assets** bundled with the integration (Home Assistant 2026.3+)
- **Diagnostics** support with automatic redaction of MAC addresses and
  serial numbers
