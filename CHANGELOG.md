# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-11

### Added
- Initial release. Full LUCI v15.x protocol implementation:
  - Persistent TCP / TLS 1.2 mutual-auth connection per speaker
  - Push-driven state updates (no polling)
  - Automatic reconnect with exponential backoff
- LSSDP network discovery on port 1800
- Zeroconf discovery via `_googlecast._tcp.local.` for Lithe-branded devices
- Config flow with three paths: scan, manual entry, zeroconf
- LS10 client certificate step with live TLS handshake validation
- Entity coverage:
  - `media_player` — play / pause / stop / next / prev / seek / volume / mute /
    source / now-playing metadata / album art / play_media
  - `button` — chimes 1–N (per-model), preset slots 1–9, reboot
  - `switch` — mute, line-in, bluetooth
  - `sensor` — source, now-playing, firmware, play state
  - `number` — volume (alternate, disabled by default)
- Services: `play_chime`, `play_preset`, `save_preset`, `delete_preset`,
  `play_direct`, `send_raw_command`, `reboot`
- Diagnostics support (redacts certs, MACs, serials)
- Bronze tier quality scale compliance
