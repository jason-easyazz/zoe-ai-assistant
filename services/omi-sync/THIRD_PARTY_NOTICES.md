# Third-party notices — services/omi-sync

`omi_sync/ring_protocol.py` (the wire layouts, command/notification opcodes, the
payload framing rule and the firmware-version gate) and the custody rules in
`omi_sync/engine.py` are a Python port of the protocol defined by Omi's MIT-licensed
source, read from a local copy of <https://github.com/BasedHardware/omi> on
2026-10-12:

- `omi/firmware/omi/src/lib/core/storage.c` — GATT service, commands, notifications
- `app/lib/services/devices/ring_protocol.dart` — the app-side codec of the same bytes
- `app/lib/services/devices/connectors/omi_connection.dart` — command/ack sequencing
- `app/lib/services/wals/ring_storage_sync.dart`, `app/lib/services/wals/PENDANT_RING_CUSTODY.md` — the custody contract
- `app/lib/services/devices/models.dart` — UUIDs

The upstream licence notice, as required by the MIT licence:

```
MIT License

Copyright (c) 2024 Based Hardware Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Runtime dependencies (installed by the operator, not vendored): `bleak` (MIT),
`opuslib` (BSD-3-Clause) over the system `libopus` (BSD-3-Clause).

The community project `j2h4u/omi-collector` (PolyForm Noncommercial) was read for
field notes only (see `docs/architecture/omi-integration-plan.md` §2.2); none of it
is used here.
