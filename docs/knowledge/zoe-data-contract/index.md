---
type: index
title: "zoe-data contract detail (OKF bundle)"
description: "Long-form records behind services/zoe-data/AGENTS.md, moved out in the 2026-10-18 DOX pass (B10): the full pre-pass text of every contract bullet, grouped by topic, plus the before/after table that proves every rule-bearing bullet kept a binding form."
tags: [zoe-data, contract, dox, okf, index]
timestamp: 2026-10-18T20:00:00+08:00
---

# zoe-data contract detail

`services/zoe-data/AGENTS.md` had grown to ~313 KB with single bullets over 11,000 characters, so no agent could read it as a contract. In the 2026-10-18 DOX pass (architecture deep dive task B10) every long bullet was cut to its binding form (owner, flags with defaults, MUST/NEVER rules, owner decisions) and its full text moved here. Nothing was deleted: the section ids `ZDn` are the original line numbers of the contract file before the pass.

## Records

- [Platform, config and engineering-loop](platform.md) — App entry and core files, the browser broker, agent security helpers, typed env parsing, the OpenClaw retirement, the engineering executor seam, logging, DB pool hardening, tool allow-lists, background loops and the bash tool. (14 records)
- [Routing, NLU, safety and sockets](routing.md) — The router head and two-stage router, deterministic matchers and command shapes, write-claim guard, home control and household lists, WebSocket role policy, push liveness, biometric scoping, actor-asserting endpoints, SSRF residuals, reminders. (22 records)
- [Brain lanes and the Flue seam](brain.md) — Flue/core/legacy lane selection, the seam recall floor, persona layer, user-model block, history hygiene, wire version, abort-on-cancel, lazy lane context and the append-only prompt prefix rule. (12 records)
- [Memory recall, floors and person memory](memory-recall.md) — Memory capture, two timelines, supersede-by-key, named-person recall, role-guess, structural floors, ask-to-remember, personalisation hop, person half, memories API, provenance, self-model, relationship graph, person merge, contacts. (19 records)
- [Memory write, forgetting and maintenance](memory-lifecycle.md) — Memory scope and extraction rules, opt-out, supersession, drawers index compaction, physical erase, identity facts, authority and own-words walls, night mind, forgetting cascade, loss-class guards, nightly digest, open loops, verbatim evidence. (30 records)
- [Voice lane, speaker identity and sessions](voice.md) — TTS/STT helpers, pacing, speaker identification, VAD, voice memory contracts, WebRTC ingest, greeting, stitch, STT under speech, utterance gate, mishear and STT snap, speculative turn start, face ID, enrolment, voice-verified panel session. (20 records)
- [Music, smart home and app connection](music.md) — Music setup bridge, smart-home bridge, browse, radio and discovery, Music Assistant idle reap, favourites, listening journal, YouTube Music sign-in, app-connection handoff, Telegram voice notes. (10 records)
- [Proactive delivery, restraint and calendar](proactive.md) — User filters, proactive spoken delivery, brief on arrival and first turn, selector, ledger, pull-not-push, restraint, distress hand-off, recall gate, mirror guard, turn quality, risk cues, calendar and Bluetooth. (18 records)
- [Omi pendant, struggle detector and loops](omi-struggle.md) — Pendant recordings, ownership, speaker turns and learned voices, day review, health heartbeat, the struggle detector and its proposals, open-loop lifecycle. (8 records)

## Before / after: every rule-bearing bullet

One row per pre-pass bullet that was condensed. `before` / `after` are character counts. Flag, module and migration names were checked mechanically: every `ZOE_*` flag in a pre-pass bullet appears in its condensed form.

| id | record | before | after |
|---|---|---|---|
| ZD11 | [SetFit and two stage router](routing.md#zd11-setfit-and-two-stage-router) | 5655 | 644 |
| ZD12 | [Conversation quality seam](routing.md#zd12-conversation-quality-seam) | 829 | 596 |
| ZD13 | [Pi intent evidence JSONL](platform.md#zd13-pi-intent-evidence-jsonl) | 1036 | 488 |
| ZD15 | [Browser broker text extraction](platform.md#zd15-browser-broker-text-extraction) | 2644 | 606 |
| ZD16 | [Agent security helpers](platform.md#zd16-agent-security-helpers) | 564 | 551 |
| ZD18 | [Research web lookup honesty](routing.md#zd18-research-web-lookup-honesty) | 2934 | 656 |
| ZD19 | [Brain callable web search](routing.md#zd19-brain-callable-web-search) | 2143 | 614 |
| ZD20 | [Honest forget and teach](memory-recall.md#zd20-honest-forget-and-teach) | 928 | 612 |
| ZD21 | [Memory capture status](memory-recall.md#zd21-memory-capture-status) | 587 | 473 |
| ZD22 | [Two memory timelines](memory-recall.md#zd22-two-memory-timelines) | 861 | 581 |
| ZD23 | [Owner retraction by key](memory-recall.md#zd23-owner-retraction-by-key) | 743 | 382 |
| ZD24 | [Memory ops and for prompt](memory-recall.md#zd24-memory-ops-and-for-prompt) | 569 | 555 |
| ZD25 | [Named person recall floor](memory-recall.md#zd25-named-person-recall-floor) | 722 | 486 |
| ZD26 | [Role guess guard](memory-recall.md#zd26-role-guess-guard) | 1002 | 476 |
| ZD27 | [Structural memory floors](memory-recall.md#zd27-structural-memory-floors) | 1513 | 594 |
| ZD28 | [Ask to remember](memory-recall.md#zd28-ask-to-remember) | 1393 | 740 |
| ZD29 | [Personalisation hop](memory-recall.md#zd29-personalisation-hop) | 1653 | 586 |
| ZD30 | [Person half guards](memory-recall.md#zd30-person-half-guards) | 1826 | 623 |
| ZD31 | [Voice lane parity](memory-recall.md#zd31-voice-lane-parity) | 1887 | 785 |
| ZD32 | [Memories screen API](memory-recall.md#zd32-memories-screen-api) | 1348 | 738 |
| ZD33 | [Provenance and memory control](memory-recall.md#zd33-provenance-and-memory-control) | 4469 | 768 |
| ZD34 | [Self model](memory-recall.md#zd34-self-model) | 1367 | 583 |
| ZD35 | [Unknown name and challenges](memory-recall.md#zd35-unknown-name-and-challenges) | 2175 | 606 |
| ZD36 | [Seam recall floor](brain.md#zd36-seam-recall-floor) | 10968 | 758 |
| ZD37 | [Persona layer phase 0](brain.md#zd37-persona-layer-phase-0) | 2639 | 798 |
| ZD38 | [User model block](brain.md#zd38-user-model-block) | 2853 | 779 |
| ZD39 | [Flue history hygiene](brain.md#zd39-flue-history-hygiene) | 1524 | 527 |
| ZD40 | [Flue wire version](brain.md#zd40-flue-wire-version) | 2191 | 645 |
| ZD41 | [Flue abort on cancel](brain.md#zd41-flue-abort-on-cancel) | 922 | 456 |
| ZD42 | [Relationship graph traversal](memory-recall.md#zd42-relationship-graph-traversal) | 1405 | 631 |
| ZD43 | [Person merge](memory-recall.md#zd43-person-merge) | 753 | 640 |
| ZD44 | [Contacts from known people](memory-recall.md#zd44-contacts-from-known-people) | 2656 | 679 |
| ZD45 | [Layout memory](routing.md#zd45-layout-memory) | 623 | 531 |
| ZD46 | [User selectable voice](voice.md#zd46-user-selectable-voice) | 1175 | 476 |
| ZD47 | [Voice capability helpers](voice.md#zd47-voice-capability-helpers) | 2752 | 614 |
| ZD48 | [Moonshine STT loader](voice.md#zd48-moonshine-stt-loader) | 1128 | 408 |
| ZD49 | [Spoken rhythm pacing guard](voice.md#zd49-spoken-rhythm-pacing-guard) | 1342 | 494 |
| ZD50 | [Speaker identification](voice.md#zd50-speaker-identification) | 4443 | 782 |
| ZD51 | [Silero VAD and smart turn](voice.md#zd51-silero-vad-and-smart-turn) | 1964 | 639 |
| ZD52 | [Voice memory and event loop](voice.md#zd52-voice-memory-and-event-loop) | 1622 | 1060 |
| ZD53 | [WebRTC audio ingest](voice.md#zd53-webrtc-audio-ingest) | 991 | 397 |
| ZD54 | [First turn greeting](voice.md#zd54-first-turn-greeting) | 1270 | 492 |
| ZD55 | [Segment stitched TTS](voice.md#zd55-segment-stitched-tts) | 1385 | 438 |
| ZD56 | [Spoken weather answers](voice.md#zd56-spoken-weather-answers) | 1359 | 508 |
| ZD57 | [STT under speech](voice.md#zd57-stt-under-speech) | 1249 | 550 |
| ZD58 | [Pre brain utterance gate](voice.md#zd58-pre-brain-utterance-gate) | 1807 | 540 |
| ZD59 | [Mishear clarification](voice.md#zd59-mishear-clarification) | 1501 | 564 |
| ZD60 | [STT snap](voice.md#zd60-stt-snap) | 2607 | 648 |
| ZD61 | [Speculative turn start](voice.md#zd61-speculative-turn-start) | 5279 | 615 |
| ZD62 | [Face ID storage](voice.md#zd62-face-id-storage) | 810 | 465 |
| ZD63 | [WebSocket role policy](routing.md#zd63-websocket-role-policy) | 1997 | 772 |
| ZD64 | [Panel push liveness](routing.md#zd64-panel-push-liveness) | 2514 | 752 |
| ZD65 | [Biometric ownership scoping](routing.md#zd65-biometric-ownership-scoping) | 1428 | 692 |
| ZD66 | [Zoe driven voice enrolment](voice.md#zd66-zoe-driven-voice-enrolment) | 4181 | 794 |
| ZD67 | [Voice signature engine load failure](voice.md#zd67-voice-signature-engine-load-failure) | 853 | 440 |
| ZD68 | [Voice verified panel session](voice.md#zd68-voice-verified-panel-session) | 3244 | 807 |
| ZD70 | [Typed env parsing](platform.md#zd70-typed-env-parsing) | 482 | 478 |
| ZD72 | [App connection handoff](music.md#zd72-app-connection-handoff) | 446 | 422 |
| ZD73 | [Telegram voice notes](music.md#zd73-telegram-voice-notes) | 1424 | 624 |
| ZD74 | [Music setup bridge](music.md#zd74-music-setup-bridge) | 3011 | 675 |
| ZD75 | [Smart home bridge](music.md#zd75-smart-home-bridge) | 2282 | 598 |
| ZD76 | [Music browse surface](music.md#zd76-music-browse-surface) | 1218 | 503 |
| ZD77 | [Music radio and discovery](music.md#zd77-music-radio-and-discovery) | 1303 | 563 |
| ZD78 | [Music Assistant idle reap](music.md#zd78-music-assistant-idle-reap) | 2308 | 625 |
| ZD79 | [Music favourites asymmetry](music.md#zd79-music-favourites-asymmetry) | 893 | 440 |
| ZD80 | [Music discovery and journal](music.md#zd80-music-discovery-and-journal) | 1862 | 682 |
| ZD81 | [YouTube Music sign in](music.md#zd81-youtube-music-sign-in) | 3461 | 799 |
| ZD82 | [Real users and proactive recipients](proactive.md#zd82-real-users-and-proactive-recipients) | 1888 | 644 |
| ZD83 | [Proactive spoken delivery](proactive.md#zd83-proactive-spoken-delivery) | 3230 | 782 |
| ZD84 | [Brief on arrival](proactive.md#zd84-brief-on-arrival) | 2555 | 773 |
| ZD85 | [Brief on first turn](proactive.md#zd85-brief-on-first-turn) | 2542 | 738 |
| ZD86 | [Proactivity selector](proactive.md#zd86-proactivity-selector) | 1773 | 699 |
| ZD87 | [No memory for unidentified turns](proactive.md#zd87-no-memory-for-unidentified-turns) | 1034 | 584 |
| ZD88 | [Dates roles and corrections](proactive.md#zd88-dates-roles-and-corrections) | 1929 | 694 |
| ZD89 | [Delivery ledger](proactive.md#zd89-delivery-ledger) | 1616 | 603 |
| ZD90 | [Pull not push](proactive.md#zd90-pull-not-push) | 2173 | 616 |
| ZD91 | [Restraint in code](proactive.md#zd91-restraint-in-code) | 3770 | 784 |
| ZD92 | [Distress hand off](proactive.md#zd92-distress-hand-off) | 1245 | 667 |
| ZD93 | [Recall relevance gate](proactive.md#zd93-recall-relevance-gate) | 1447 | 640 |
| ZD94 | [Mirror guard](proactive.md#zd94-mirror-guard) | 1164 | 604 |
| ZD95 | [Turn quality](proactive.md#zd95-turn-quality) | 2798 | 632 |
| ZD96 | [Risk cue tier](proactive.md#zd96-risk-cue-tier) | 1935 | 638 |
| ZD97 | [Health events private](proactive.md#zd97-health-events-private) | 873 | 412 |
| ZD98 | [Calendar data model](proactive.md#zd98-calendar-data-model) | 1584 | 672 |
| ZD99 | [Bluetooth settings broker](proactive.md#zd99-bluetooth-settings-broker) | 871 | 619 |
| ZD100 | [Omi pendant recordings](omi-struggle.md#zd100-omi-pendant-recordings) | 4173 | 1140 |
| ZD101 | [Pendant ownership registry](omi-struggle.md#zd101-pendant-ownership-registry) | 2166 | 744 |
| ZD102 | [Omi speaker turns voices](omi-struggle.md#zd102-omi-speaker-turns-voices) | 4916 | 813 |
| ZD103 | [Pendant day review](omi-struggle.md#zd103-pendant-day-review) | 5190 | 789 |
| ZD104 | [Pendant health warnings](omi-struggle.md#zd104-pendant-health-warnings) | 2836 | 1018 |
| ZD105 | [Struggle detector phase 1](omi-struggle.md#zd105-struggle-detector-phase-1) | 2345 | 770 |
| ZD106 | [Struggle proposals phase 2](omi-struggle.md#zd106-struggle-proposals-phase-2) | 2640 | 751 |
| ZD107 | [Open loop lifecycle](omi-struggle.md#zd107-open-loop-lifecycle) | 1878 | 574 |
| ZD112 | [requirements txt is a spec](platform.md#zd112-requirements-txt-is-a-spec) | 3230 | 647 |
| ZD113 | [OpenClaw retirement](platform.md#zd113-openclaw-retirement) | 858 | 578 |
| ZD114 | [Executor board single seam](platform.md#zd114-executor-board-single-seam) | 1713 | 598 |
| ZD115 | [Application logging setup](platform.md#zd115-application-logging-setup) | 2226 | 623 |
| ZD116 | [Brain lane failover](brain.md#zd116-brain-lane-failover) | 6940 | 635 |
| ZD117 | [Brain dispatch source of truth](brain.md#zd117-brain-dispatch-source-of-truth) | 776 | 572 |
| ZD118 | [Lazy lane context loader](brain.md#zd118-lazy-lane-context-loader) | 1192 | 608 |
| ZD119 | [Append only prompt prefixes](brain.md#zd119-append-only-prompt-prefixes) | 4746 | 636 |
| ZD120 | [SSE AG UI protocol module](brain.md#zd120-sse-ag-ui-protocol-module) | 746 | 619 |
| ZD121 | [Hermes stream module](brain.md#zd121-hermes-stream-module) | 661 | 414 |
| ZD124 | [Ambient memory ownership](memory-lifecycle.md#zd124-ambient-memory-ownership) | 555 | 459 |
| ZD125 | [Extraction mines user turn only](memory-lifecycle.md#zd125-extraction-mines-user-turn-only) | 502 | 317 |
| ZD126 | [Anaphoric capture](memory-lifecycle.md#zd126-anaphoric-capture) | 594 | 434 |
| ZD127 | [Memory opt out](memory-lifecycle.md#zd127-memory-opt-out) | 2137 | 592 |
| ZD128 | [Cross writer supersession](memory-lifecycle.md#zd128-cross-writer-supersession) | 1541 | 471 |
| ZD129 | [Semantic search unfiltered first](memory-lifecycle.md#zd129-semantic-search-unfiltered-first) | 1033 | 380 |
| ZD130 | [Drawers index compaction](memory-lifecycle.md#zd130-drawers-index-compaction) | 5917 | 689 |
| ZD131 | [Physical erase of forgotten text](memory-lifecycle.md#zd131-physical-erase-of-forgotten-text) | 2459 | 748 |
| ZD132 | [Identity is an account fact](memory-lifecycle.md#zd132-identity-is-an-account-fact) | 1920 | 727 |
| ZD133 | [Spoken arithmetic fast tier](memory-lifecycle.md#zd133-spoken-arithmetic-fast-tier) | 889 | 469 |
| ZD134 | [Whats my name on Skybridge](memory-lifecycle.md#zd134-whats-my-name-on-skybridge) | 356 | 352 |
| ZD135 | [Session status voice answer](memory-lifecycle.md#zd135-session-status-voice-answer) | 946 | 472 |
| ZD136 | [Quote backed retirement](memory-lifecycle.md#zd136-quote-backed-retirement) | 1349 | 659 |
| ZD137 | [Own words wall](memory-lifecycle.md#zd137-own-words-wall) | 1177 | 639 |
| ZD138 | [Memory authority](memory-lifecycle.md#zd138-memory-authority) | 3647 | 795 |
| ZD139 | [Contrast corrections](memory-lifecycle.md#zd139-contrast-corrections) | 674 | 358 |
| ZD140 | [Exact words and two hop recall](memory-lifecycle.md#zd140-exact-words-and-two-hop-recall) | 2041 | 722 |
| ZD141 | [Night mind](memory-lifecycle.md#zd141-night-mind) | 1929 | 581 |
| ZD142 | [Forgetting stays forgotten](memory-lifecycle.md#zd142-forgetting-stays-forgotten) | 2150 | 759 |
| ZD143 | [Forget alias sweep](memory-lifecycle.md#zd143-forget-alias-sweep) | 1808 | 590 |
| ZD144 | [Forgetting reaches the box](memory-lifecycle.md#zd144-forgetting-reaches-the-box) | 811 | 543 |
| ZD145 | [Contacts data model](memory-lifecycle.md#zd145-contacts-data-model) | 1944 | 765 |
| ZD146 | [Forget that cannot reach a store](memory-lifecycle.md#zd146-forget-that-cannot-reach-a-store) | 2400 | 792 |
| ZD147 | [Memory loss class guards](memory-lifecycle.md#zd147-memory-loss-class-guards) | 1834 | 584 |
| ZD148 | [Implicit supersede](memory-lifecycle.md#zd148-implicit-supersede) | 1391 | 626 |
| ZD149 | [Fact to person linkage](memory-lifecycle.md#zd149-fact-to-person-linkage) | 944 | 454 |
| ZD150 | [Memory loop observability](memory-lifecycle.md#zd150-memory-loop-observability) | 5270 | 650 |
| ZD151 | [Nightly passes read whole day](memory-lifecycle.md#zd151-nightly-passes-read-whole-day) | 1365 | 501 |
| ZD152 | [Open loops extraction](memory-lifecycle.md#zd152-open-loops-extraction) | 2317 | 599 |
| ZD153 | [Verbatim evidence and recall](memory-lifecycle.md#zd153-verbatim-evidence-and-recall) | 3253 | 612 |
| ZD154 | [DB pool hardening](platform.md#zd154-db-pool-hardening) | 633 | 506 |
| ZD155 | [Write path exception logging](platform.md#zd155-write-path-exception-logging) | 1137 | 528 |
| ZD156 | [Skybridge command not keyword](routing.md#zd156-skybridge-command-not-keyword) | 2650 | 576 |
| ZD157 | [Everyday command shapes](routing.md#zd157-everyday-command-shapes) | 4273 | 606 |
| ZD158 | [Router head authority intent gate](routing.md#zd158-router-head-authority-intent-gate) | 3743 | 576 |
| ZD159 | [Write claim guard](routing.md#zd159-write-claim-guard) | 3105 | 647 |
| ZD160 | [Home control needs member or panel](routing.md#zd160-home-control-needs-member-or-panel) | 2861 | 774 |
| ZD161 | [Household lists fridge rule](routing.md#zd161-household-lists-fridge-rule) | 4257 | 830 |
| ZD162 | [Evidence questions routing](routing.md#zd162-evidence-questions-routing) | 1456 | 508 |
| ZD163 | [Event time and present state](routing.md#zd163-event-time-and-present-state) | 1710 | 557 |
| ZD164 | [Explicit date over weekday](routing.md#zd164-explicit-date-over-weekday) | 728 | 501 |
| ZD166 | [No fork on event loop](platform.md#zd166-no-fork-on-event-loop) | 1516 | 598 |
| ZD167 | [Guarded subprocess and fetch](platform.md#zd167-guarded-subprocess-and-fetch) | 710 | 643 |
| ZD168 | [WebSocket origin guard](routing.md#zd168-websocket-origin-guard) | 771 | 576 |
| ZD169 | [Known SSRF residuals](routing.md#zd169-known-ssrf-residuals) | 764 | 576 |
| ZD170 | [Actor asserting endpoints gate](routing.md#zd170-actor-asserting-endpoints-gate) | 1576 | 580 |
| ZD173 | [Temporal person relationships](routing.md#zd173-temporal-person-relationships) | 1037 | 541 |
| ZD175 | [Reminders write and fire seam](routing.md#zd175-reminders-write-and-fire-seam) | 3782 | 633 |
| ZD176 | [HA media target resolution](platform.md#zd176-ha-media-target-resolution) | 589 | 525 |
| ZD177 | [Capability prose honesty](platform.md#zd177-capability-prose-honesty) | 1141 | 621 |

Total of condensed bullets at the first pass: 308224 chars before, 94114 after (the per-row `after` column is the first-pass size). A full cross-vendor review on 2026-10-19 (the '-' lines of the contract diff were these originals) found that the first pass had dropped binding clauses in many bullets; those were restored, so `services/zoe-data/AGENTS.md` is now about 173 KB (from ~314 KB). Bullets that landed on main after the pass (A10 LiveKit rooms, A11 per-turn panel channel, change notices P1, A2 mutating-route auth, music reads auth #2121) sit whole in `services/zoe-data/AGENTS.md` and have no record here.
