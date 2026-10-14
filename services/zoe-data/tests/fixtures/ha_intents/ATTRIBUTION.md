# Attribution: phrasing regression corpus

`en.jsonl` in this directory is test data derived from two third-party projects. It
is used only as input to `services/zoe-data/tests/test_ha_phrasing_corpus.py`; it is
not shipped in Zoe's runtime. Regenerate it with
`scripts/maintenance/ha_intents_corpus.py` (pins in `sources.json`).

## Home Assistant intents (rows with `src` = `ha_test` or `ha_sample`)

- Source: Home Assistant intents, https://github.com/OHF-Voice/intents (formerly
  `home-assistant/intents`), commit `c602e0b215d9ae5e7b8e8b88a93027d55e989cab`
  (OHF-Voice/intents, 2026-10-10).
- Copyright: © Home Assistant / Open Home Foundation contributors.
- Licence: Creative Commons Attribution 4.0 International (CC-BY-4.0),
  https://creativecommons.org/licenses/by/4.0/ (the repository's `LICENSE.md`).
- **Modified.** What was done to the material:
  - `ha_test` rows are the English test sentences from `tests/en/<Intent>/*.yaml`, copied
    verbatim with HA's intent name as the label. A `HassStartTimer` sentence that carries
    a delayed command is relabelled `HassStartTimer.delayed_command`; timer sentences get a
    `seconds` field computed from HA's hours/minutes/seconds slots. Slots, areas,
    entities and responses were dropped.
  - `ha_sample` rows were generated from HA's English sentence templates with HA's own
    sampler (`python -m script.intentfest sample`, hassil 3.12.1), then sub-sampled with a
    fixed seed and de-duplicated against the test sentences.
  - `mapping.yaml` `relabel` moves five "outside temperature" test sentences from
    `HassGetState` to `HassGetWeather` for Zoe's scoring.
  - The labels are scored against Zoe-specific expectations (`mapping.yaml`), which are
    Zoe's, not Home Assistant's.
- No endorsement by Home Assistant or the Open Home Foundation is implied.

## OVOS weather skill (rows with `src` = `ovos`)

- Source: OpenVoiceOS weather skill, https://github.com/OpenVoiceOS/ovos-skill-weather,
  commit `991439b0968408e92f9de9b3176be4ddb18163e9` (OpenVoiceOS/ovos-skill-weather,
  2026-10-09), files `locale/en-US/intents/*.intent`.
- Licence: Apache License 2.0; the full text is in `LICENSE-Apache-2.0.txt` next to this
  file, as section 4(a) requires.
- NOTICE: the upstream repository ships no `NOTICE` file at that commit, so there is no
  NOTICE text to carry.
- **Modified** (Apache-2.0 section 4(b)): each template line was expanded into at most three
  concrete sentences, chosen with a fixed seed; the required slots `{location}` and `{unit}`
  were filled with the fixed public values `Paris` and `celsius`; the intent file name became
  the label (`OVOS.<file>`); duplicates were removed.

This is a reading of the two licences, not legal advice.
