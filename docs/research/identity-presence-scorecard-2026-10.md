---
type: research
title: "Identity as a score card (2026-10-14): standards, shipped products, presence, unknown speakers, anti-spoofing, law, and what Zoe has"
date: 2026-10-14
timestamp: 2026-10-14T00:00:00+08:00
status: research-only. No code, flag, service, unit, table or live path was changed. Not legal advice; section 9 lists what needs a lawyer. All examples use invented names.
description: Owner request (2026-10-14) for identity to be a score card (last login, left the room, phone in range, voice match, face match), set per user in their own settings, with Zoe noticing new voices and learning a visitor once introduced. This record holds the internet research and the code survey. The design is docs/architecture/identity-presence-engine.md.
tags: [identity, presence, authentication, biometrics, speaker-id, face-id, ble, nist, fido, privacy, law, research]
evidence_labels: "[verified] the primary page was fetched and read on 2026-10-14 (the fetch tool returns a summarised view; re-read exact legal wording before relying on it) | [verified-re] a load-bearing quote re-fetched and grepped verbatim by the author | [secondary] a reputable secondary source, or a primary page seen only via search results | [unverified] a search snippet or memory | [src] file:line read in this worktree (origin/main b02a04a7b) | [branch] read from unmerged PR #2024"
---

# Identity as a score card (2026-10-14)

Owner request, 2026-10-14 (gist):

- Security as "a score card, taking into account factors: when did the person last login, have they left
  the room, is their phone in range, does their voice match, does the camera/visual reference match".
- Zoe aware of who is there, flagging new people she hears, and recognising and learning about someone once
  introduced.
- All of it in each user's login settings ("no password, password, PIN, voice, visual, etc.").

Design that follows from this record: [identity-presence-engine.md](../architecture/identity-presence-engine.md).
Earlier records it builds on: [panel-identity-plan.md](../architecture/panel-identity-plan.md) (July, biometric
split and fusion sketch), [presence-without-sensors-2026-10-04.md](./presence-without-sensors-2026-10-04.md)
(occupancy design, BLE/IRK, camera power), the [speaker bake-off](./speaker-model-bakeoff-2026-10.md) and the
[ResNet34 record](./speaker-resnet34-optimisation-2026-10.md).

Method: a research sub-agent (Sonnet) ran the web sweep. I re-fetched the load-bearing NIST quotes myself
(`[verified-re]`). A second sub-agent ran the code survey, and I spot-checked it (section 10).

**Could not be reached** (said plainly so nobody reads a check into it):

- ilga.gov (BIPA text): connection refused.
- austlii / legislation.gov.au Act text: 403, or a table of contents only.
- federalregister.gov.
- The HSF Kramer article.
- Amazon Visual ID and Voice ID help pages: 503 on fetch.
- CTAP 2.2 §11.5: truncated.

## 0. Answer first: the twelve findings that shape the design

1. **NIST forbids voice as an authenticator.** SP 800-63B-4 §3.2.3: "Biometric comparison based on voice
   SHALL NOT be used." [verified-re] Biometrics in general "SHALL only be used as part of multi-factor
   authentication with a physical authenticator", at an FMR of "one in 10000 or better". "Voice is a
   password" can therefore only be a household-convenience choice, never a factor for high-risk actions.
2. **A score card is an indicator layer, not a factor.** NIST: risk and fraud indicators "do not impact or
   change the AAL" [verified]. Zero trust (SP 800-207) asks for per-session, dynamic, attribute-based policy
   [verified]. So the fused score decides **when to ask**; knowledge and possession factors still guard the
   floors.
3. **Every major vendor ships the cut-down form.** Google Voice Match and Face Match (Face Match "not a
   security feature", photo and look-alike warnings), Alexa voice ID, and HomePod personal requests all
   personalise. Purchases and security use a PIN, a voice code or the phone. None ships a user-tunable fused
   score [verified / secondary].
4. **Events beat timers.** Microsoft calls short token lifetimes a "blunt object". Continuous Access
   Evaluation revokes on events in near real time [verified]. Windows Dynamic Lock locks on phone RSSI loss.
   Android on-body detection locks within about a minute [verified]. Design consequence: a different voice
   or leaving the room should cut confidence at once.
5. **Likelihood-ratio fusion is the principled, explainable choice.** It is Neyman-Pearson optimal when the
   densities are right (Nandakumar et al., TPAMI 2008) [verified abstract]. The sum rule is more robust to
   bad density estimates (Kittler) [secondary]. A **clipped log-odds sum** is the practical middle when the
   household has little data.
6. **BLE presence is noisy and needs a key.** RSSI suffers body shadowing and multipath [verified]. Apple
   rotates its BLE address about every 15 minutes [verified], so following a phone needs its IRK, which is a
   technical, hand-held enrolment step [verified]. There is no published room-level accuracy figure, so it
   must be measured in the house. Wi-Fi trackers lie when phones sleep [secondary].
7. **mmWave answers "someone is still here" better than RSSI.** It costs A$38-80 per room and needs no camera
   or phone [secondary; presence record].
8. **A diarisation model is not needed for "a different person is talking".** pyannote community-1 reaches a
   DER of 11-27 % on standard sets and is fast on an H100 [verified]. There is no Jetson figure, and it is
   built for recordings. Zoe's turns are already single-speaker segments with an embedding, so open-set
   matching plus online clustering of unknown embeddings is enough.
9. **Learning over time is the sharp edge.** Siri's implicit enrolment caps at 40 vectors and warns that
   impostor utterances "could corrupt the profile" [verified]. Frigate cannot unlearn a wrong face
   [verified]. A visitor profile must be seeded by an explicit introduction, updated only from strong
   matches, and be easy to undo.
10. **Replay and cloning are the realistic home attacks.** Countermeasures do not generalise:
    - ASVspoof 2021 PA detectors lack robustness in real rooms [verified];
    - ASVspoof 5 baselines sit above 29 % EER [secondary];
    - an AI-cloned voice passed a bank's voice check in 2023 [secondary].

    Do not lean on an anti-spoofing model. Keep voice off the floors.
11. **Biometric templates are sensitive information under the Australian Privacy Act.** Consent is required
    and cannot be implied (OAIC) [verified]. The household exemption (s 16) speaks of an individual's own
    household affairs [secondary, text unread]. Whether it covers running Zoe at home, any distribution of Zoe
    to other households, or visitors is an unresolved question for counsel. The WA Surveillance Devices Act needs each principal party's consent to record a
    private conversation [verified]. **A lawyer is needed before visitor voiceprints or any distribution.**
12. **Retention precedent exists.** Amazon deletes a Visual ID unrecognised for 18 months [secondary]. Google
    leaves Face Match enrolment images after the model is removed, which is a trap to avoid [verified].

## 1. Standards: NIST SP 800-63B-4, zero trust, continuous evaluation

- [verified] **Status.** SP 800-63-4 is final, dated 31 July 2025, and supersedes 800-63-3, which was
  "Withdrawn on August 01, 2025". [verified-re] The same CSRC page carries "Planning Note (10/06/2026): The
  Implementation Resource Hub for SP 800-63-4 is now available!" and lists the 07/31/25 final in its document
  history (https://csrc.nist.gov/pubs/sp/800/63/4/final, fetched 2026-10-14).
  https://csrc.nist.gov/pubs/sp/800/63/4/final ;
  https://csrc.nist.gov/News/2025/nist-revises-digitial-identity-guidelines-sp-800-6 (dated 2025-08-01).
- [verified] **Assurance levels** (https://pages.nist.gov/800-63-4/sp800-63b.html §2, a live document):
  - AAL1 "provides basic confidence that the claimant controls an authenticator bound to the subscriber
    account";
  - AAL2 "high confidence", two distinct factors;
  - AAL3 "very high confidence", a phishing-resistant cryptographic authenticator with a non-exportable key.
- [verified-re] **Reauthentication** (same page):

  | AAL | Overall timeout | Inactivity timeout |
  |---|---|---|
  | AAL1 | "SHOULD be no more than 30 days" | "MAY be applied but is not required" |
  | AAL2 | "SHOULD be no more than 24 hours" | "SHOULD be no more than 1 hour" |
  | AAL3 | "SHALL be no more than 12 hours" | "SHOULD be no more than 15 minutes" |

  At AAL2, after an inactivity timeout, the verifier "MAY allow the subscriber to reauthenticate using only a
  successful password or biometric comparison in conjunction with the session secret". "Session activity
  SHALL reset the inactivity timeout."
- [verified-re] **Biometrics** (https://pages.nist.gov/800-63-4/sp800-63b/authenticators/ §3.2.3):
  - "Biometrics SHALL only be used as part of multi-factor authentication with a physical authenticator".
  - "The biometric system SHALL operate with an FMR ... of one in 10000 or better for all demographic
    groups"; FNMR "SHOULD ... [be] less than 5 %".
  - "SHOULD implement PAD for iris and fingerprint modalities and SHALL implement PAD for facial
    recognition".
  - **"Biometric comparison based on voice SHALL NOT be used."**
  - Comparison "SHOULD be performed locally".
  - Retry limits: "no more than five consecutive failed authentication attempts or 10 ... if PAD is
    implemented", then "a delay of at least 30 seconds before each subsequent attempt", with "an overall
    limit of no more than 50 ... or 100 if PAD is implemented".
- [verified] **Session monitoring (§5.3, "sometimes called continuous authentication")** is allowed "as a
  risk reduction measure". The signals it names are usage patterns, behavioural biometrics, device traits,
  geolocation and IP. Their processing "SHALL be included in the privacy risk assessment". Fraud indicators
  "do not impact or change the AAL".
- [verified] **"Continuous evaluation" in 800-63-4** is a programme-level step ("Continuously evaluate and
  improve"), not a per-session score. https://pages.nist.gov/800-63-4/sp800-63.html
- [verified] **NIST SP 800-207, zero trust (August 2020)**,
  https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-207.pdf. Four tenets map onto a home:
  - "Network location alone does not imply trust" (so not "on the home LAN, so trusted").
  - Access is "granted on a per-session basis".
  - Access is "determined by dynamic policy" including "behavioral and environmental attributes".
  - Authentication is "dynamic and strictly enforced" in a "constant cycle of ... continually reevaluating
    trust". Policy varies "based on the sensitivity of the resource".
- [verified] **Microsoft Entra Continuous Access Evaluation**
  (https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation,
  updated 2026-09-22):
  - Shortening token lifetimes is a "blunt object".
  - Critical events (account disabled, password changed, admin revoke, high user risk) are evaluated "near
    real time", up to 15 minutes of latency.
  - Lesson for Zoe: event-driven revocation.
- [verified] **Entra ID Protection**
  (https://learn.microsoft.com/en-us/entra/id-protection/concept-identity-protection-risks, 2026-04-22): risk
  models need a learning period, a minimum of 5 days for unfamiliar sign-in properties, and 14 days or 10
  logins for atypical travel. Lesson: a new signal needs a bootstrap or shadow period before it counts.
- [secondary] **Continuous authentication surveys:** Abuhamad et al., IEEE IoT Journal 2021, surveyed 140+
  behavioural-biometric approaches (https://arxiv.org/pdf/2001.08578); Stylios and Thanou 2016
  (https://www.academia.edu/28051990/A_Review_of_Continuous_Authentication_Using_Behavioral_Biometrics).
- [verified] **Windows Dynamic Lock**
  (https://learn.microsoft.com/en-us/windows/security/identity-protection/hello-for-business/hello-feature-dynamic-lock,
  updated 2026-10-08):
  - It locks when a paired phone's RSSI drops, and only when the PC is idle.
  - Defaults `rssiMin="-10"`, `rssiMaxDelta="-10"`.
  - "An additional barrier. It doesn't replace the need for the user to lock the computer."
  - It only locks, never unlocks.
  - [secondary] About 30 s (https://www.pcworld.com/article/3210834/this-windows-security-trick-auto-locks-your-pc-when-you-leave.html);
    "within a minute" per Microsoft support (snippet).

## 2. Shipped products

**Google**

- [verified] **Voice Match** (https://support.google.com/assistant/answer/9071681?hl=en):
  - a non-matching voice is a "guest query" and "won't provide personal results";
  - up to 6 people per home;
  - with more than 6 members it advises prioritising "children or guests".
  - [secondary] If nobody turns Voice Match on, anyone gets personal results.
- [verified] **Face Match, Nest Hub Max** (https://support.google.com/googlehome/answer/9320885?hl=en-AU):
  - "Not a security feature";
  - it "may not be able to tell the difference between you and a photograph of your face";
  - "If a household member looks very similar to you, the device may identify them as you";
  - it requires Voice Match first;
  - turning Face Match off removes the model but **not** the enrolment images, which are deleted separately.
  - [secondary] Camera sensing is analysed on-device (https://support.google.com/googlenest/answer/9449279).
- [verified] **Android Extend Unlock / Smart Lock** (https://support.google.com/android/answer/9075927?hl=en):
  - it keeps the phone unlocked "for up to 4 hours at a time";
  - on-body detection takes "up to one minute to automatically lock" after the phone is put down;
  - a trusted place is "up to about 100 meters", and location "can be copied or manipulated";
  - someone "could keep your device unlocked by imitating your Bluetooth connection".
  - [unverified] The "2024 changes" could not be confirmed (Hexnode blog:
    https://www.hexnode.com/blogs/what-is-android-smart-lock/).

**Amazon**

- [secondary] **Visual ID** (https://www.amazon.com/gp/help/customer/display.html?nodeId=GVPZZGGU8N6A9QJK, 503
  on fetch; search snippet):
  - on-device matching, opt-in, off by default;
  - enrolment images in Amazon's cloud (encrypted), and the numeric ID "may be considered biometric data in
    some locations";
  - **auto-deleted if not recognised for 18 months**;
  - Amazon's pages conflict on image storage.
- [secondary] **Voice ID** (https://www.amazon.com/gp/help/customer/display.html?nodeId=201602230):
  - voice recordings are used "to update that model over time";
  - about 10 enrolment phrases at launch, up to 10 voices per household
    (https://www.etcentric.org/amazons-alexa-now-recognizes-up-to-ten-different-voices/).
- [verified] **Unrecognised speaker** (https://developer.amazon.com/docs/custom-skills/add-personalization-to-your-skill.html):
  - "Alexa treats the speaker as a generic user for the Amazon account";
  - no person object is passed.
- [verified] **Voice purchasing** (https://digprjsurvey.amazon.com/csad/help/node/GKW8N2S4UQ2SAEBL):
  - voice ID "ensures that only recognized household members place orders";
  - "Only recognized voices" excludes digital purchases.
  - [secondary] A 4-digit spoken voice code confirms purchases and can be overheard
    (https://digprjsurvey.amazon.com/csad/help/node/GAA2RYUEDNT5ZSNK).

**Apple**

- [verified] **HomePod voice recognition** (https://support.apple.com/en-ie/guide/homepod/apd1841a8f81/homepod):
  set up per person on their own iPhone; up to six people; Personal Requests toggled per HomePod.
  - [secondary] It needs the iPhone on the same Wi-Fi. No Apple page requires the phone to be physically
    near (https://9to5mac.com/2023/03/17/send-help-homepod-personal-requests-not-working/).
- [verified] **Mac Auto Unlock with Apple Watch** (https://support.apple.com/en-us/HT206995):
  - it needs Wi-Fi and Bluetooth, the same account with two-factor authentication, and a passcoded watch;
  - "While you're wearing Apple Watch, your Mac can sense when you're nearby";
  - the first login after a restart needs the password;
  - no distance is stated.
- [verified] **"Personalized Hey Siri", 2018-04-16** (https://machinelearning.apple.com/research/personalized-hey-siri):
  - explicit enrolment of 5 utterances;
  - implicit enrolment adds accepted utterances above a threshold, **up to 40 vectors**;
  - "Wrongly accepted utterances from impostors could corrupt the profile."

**Others**

- [verified] **Frigate face recognition** (https://docs.frigate.video/configuration/face_recognition):
  - `unknown_score` 0.8, `recognition_threshold` 0.9, `min_area` 750 px;
  - multi-frame weighting, with larger faces counting more;
  - 5-10 training images minimum, 20-30 good;
  - **no negative training** (it cannot learn "this is not X").
- [secondary] **Attention gating** (Nest shows personal content only when a recognised face looks at the
  screen) was recorded in [panel-identity-plan.md](../architecture/panel-identity-plan.md) (July sweep).

## 3. FIDO, passkeys and the phone as an authenticator

- [verified] **The FIDO Proximity Exchange Protocol (PXP) working draft, 2026-07-17**
  (https://fidoalliance.org/specs/hybrid/proximity-exchange-protocol-v1.0-wd-20260717.html), the successor home
  of the hybrid ("caBLE") flow:
  - "PXP requires a proof of proximity to help prevent attacks";
  - the notification comes "in the form of a BLE advertisement";
  - the QR code "contains a public key and a shared secret key";
  - the draft is "not intended to be a basis for any implementations".
- [secondary] **CTAP 2.3**, published 2026-02-26, adds BLE as a data channel
  (https://developers.yubico.com/CTAP/CTAP2.3.html). Hybrid moving to PXP:
  https://www.corbado.com/blog/proximity-exchange-protocol-pxp.md.
- [verified] **FIDO Biometrics Requirements v4.0 (final, 2024-05-22)**
  (https://fidoalliance.org/specs/biometric/requirements/Biometrics-Requirements-v4.0-fd-20240522.html):
  - [verified-re] The requirements-by-level table gives a **lab-tested FAR of 1 % for BioLevel 1 and
    BioLevel 2** (25 test subjects) and **0.01 % for BioLevel 1+ and BioLevel 2+** (245 subjects). BioLevel 1
    and 2 also require a documented vendor self-attestation of a FAR of 1:10,000 or better.
  - What separates Level 1 from Level 2 is presentation-attack resistance, not FAR: "IAPAR of less than or
    equal to 15 %" meets BioLevel 1 and 1+, and 7 % or less meets BioLevel 2 and 2+.
  - FRR: under 5:100 for 1+ and 2+ ("upper bound of an 80% confidence interval").
  - Caveat: the document's prose FAR section groups the levels differently (1 and 1+ "less than 1:100", 2 and
    2+ "less than 1:10,000"). The table and the subject counts (25 subjects cannot demonstrate 0.01 %) support
    the reading above. Re-read the spec before quoting a level.
  - [secondary] v5.0 is a release candidate dated 2026-09-01.
- **Design relevance** [inference]:
  - The phone is the household's natural **possession factor**. A passkey with hybrid transport is the
    standards-grade version.
  - A Telegram Approve tap is an account login, which is weaker but already planned in the action-agent
    record.
  - A BLE advert proves proximity of a device, not the presence of its owner.

## 4. Multimodal score fusion

- [verified] **Nandakumar, Chen, Dass, Jain, "Likelihood ratio-based biometric score fusion", IEEE TPAMI
  30(2):342-347, Feb 2008, DOI 10.1109/TPAMI.2007.70796**
  (https://researchportal.hw.ac.uk/en/publications/likelihood-ratio-based-biometric-score-fusion/):
  - "a framework for the optimal combination of match scores that is based on the likelihood ratio test";
  - genuine and impostor densities are modelled as Gaussian mixtures.
  - [secondary] The LR test "maximizes the genuine accept rate at any desired false accept rate"
    (Neyman-Pearson). On WVU data, GAR was 85.3 % at FAR 0.001 % against 66.7 % for the best single modality
    (MSU thesis, https://d.lib.msu.edu/etd/39012/FULL_TEXT/view).
- [secondary] **Comparisons:**
  - The product of likelihood ratios and logistic regression did best
    (https://www.scitepress.org/Papers/2009/22322/22322.pdf). The product for independent matchers is a
    log-odds sum, which is the explainability argument.
  - Ross, Nandakumar, Jain, Pattern Recognition 38 (2005), score normalisation: min-max and z-score are
    outlier-sensitive, tanh is robust (cited via https://arxiv.org/pdf/1011.6220).
  - Kittler et al. (via NIST IR 7346 appendix C, https://www.nist.gov/document/ir7346appendixcpdf): the sum
    rule beat product, min, max and median "due to its resilience to errors in the estimation of the
    densities".
- [secondary] **ISO/IEC TR 24722:2024** (published 2024-12-04, https://store.sfs.fi/en/isoiec-tr-24722-2024) is a
  technical report on multimodal and multi-biometric fusion at feature, score, rank and decision level. It is
  not a normative standard, and the full text was not read.
- [unverified] **Fusion levels** (memory): sensor or feature, score, rank, decision. Score level is the usual
  sweet spot: no raw data is needed and it can be calibrated. Decision level (AND/OR of accept bits) throws
  away information and cannot express "fairly sure".
- **Calibration.** Score-to-LLR calibration by linear or logistic regression is standard practice in
  speaker recognition (the BOSARIS-style idea) [unverified, memory]. The ResNet34 record already uses
  cohort-mean centring and per-length thresholds [record]. AS-norm stabilises thresholds against an impostor
  cohort, and domain mismatch is the main calibration failure [secondary: https://arxiv.org/pdf/2007.07689,
  https://arxiv.org/html/2504.04512v1].
- **Reading for Zoe:**
  - Use a per-person log-likelihood-ratio sum, with each source's LLR from its own genuine and impostor
    distributions.
  - Clip each source group: this is the robustness the sum rule buys, and a cheap guard against correlated
    sources.
  - Give an uncalibrated source zero weight.
  - The July plan's z-score weighted sum (`panel-identity-plan.md`) is a score-level method too, but it
    needs hand weights and cannot put a PIN or a phone on the same scale.

## 5. Presence

- [verified] **BLE RSSI accuracy**: Naghdi and O'Keefe, Sensors 22(12):4320, 2022-06-07
  (https://pmc.ncbi.nlm.nih.gov/articles/PMC9227766/). "RSSI fluctuations caused by human body shadowing";
  "position accuracy is poor because the signal strength can be affected by multipath, interference, and
  shadowing". [unverified] The paper also quotes 8.7 m and 12.3 m figures whose context was unclear, and
  cites a study with less than 2.6 m at 95 % for dense beacons.
- [secondary] A sub-1 GHz (not BLE) study reached 46-89 % room accuracy with 3-4 receivers
  (https://eudl.eu/pdf/10.1007/978-3-030-70572-5_4). **No clean BLE room-level figure exists. Measure it in
  the house.**
- [verified] **Address rotation**: Martin et al., PETS 2019 (https://arxiv.org/pdf/1904.10600). Apple rotates
  BLE addresses on a 15-minute schedule, and predictable sequence numbers allow tracking across rotations.
  - [secondary] The default RPA timeout is 900 s, settable from 1 s to 1 h
    (https://docs.zephyrproject.org/2.7.4/reference/kconfig/CONFIG_BT_RPA_TIMEOUT.html).
- [verified] **HA Private BLE Device** (https://www.home-assistant.io/integrations/private_ble_device/): the IRK
  lets HA "work out which random MAC addresses are related to the IRK".
  - iOS: the IRK comes from a Mac's Keychain.
  - Rooted Android: `bt_config.conf`.
  - Otherwise: an HCI snoop log.
  - This is an operator step and conflicts with "members never run code" unless the panel drives it.
- [verified] **Bermuda** (https://github.com/agittins/bermuda): area-level BLE tracking, iBeacon plus IRK,
  "working reasonably well" (no figure), triangulation "(eventually)", needs ESPHome proxies or Shelly.
  [verified] **ESPresense** (https://espresense.com/): one node per room; "Companion" mode needs "dense
  coverage (5-8 per floor)".
- [verified] **HA `person`** (https://www.home-assistant.io/integrations/person/): connection trackers beat
  GPS, and "Since the stationary trackers have priority, you are considered home."
  - [secondary] Router trackers give a false `not_home` while phones sleep. `consider_home` defaults to 180 s
    and is often raised to 600 s or more (https://www.home-assistant.io/getting-started/presence-detection/).
- [secondary] **mmWave**:
  - Aqara FP2: 60 GHz, zones, up to 5 people (https://devices.esphome.io/devices/Aqara-FP2/).
  - LD2410: 24 GHz, still and moving to about 6 m, no counting (https://www.atomic14.com/esp32/sensors/hlk-ld2410/).
  - Tuning is needed for fans and curtains.
  - Prices and our sleep-gate fit are in the presence record §0.
- [unverified] **"Left the room" without sensors.** No source found. The nearest precedents are Android
  on-body (lock within about 1 minute) and Dynamic Lock (about 30-60 s). The inference stack (silence, no
  touch, no face for N minutes → decay, and a new voice → break) is design inference, which is why the engine
  treats absence as decay only and treats a **new voice** as the one strong "presence changed" event Zoe can
  observe today.

## 6. The camera and face

- [verified] NIST requires PAD for face (section 1). [verified] Google warns about photos and look-alikes
  (section 2).
- Already measured or recorded in the repo:
  - the face pipeline (SCRFD det_500m plus MobileFaceNet, 512-d, all on the Pi);
  - liveness (MiniFASNetV2, about 37 ms on a Pi CPU, in [panel-identity-plan](../architecture/panel-identity-plan.md));
  - **the Pi 5 USB power trap**, 600 mA versus 1.6 A only on a negotiated 5 A supply
    ([presence record §0](./presence-without-sensors-2026-10-04.md)).
- Nothing new is needed from the web until the owner decides B4.3.

## 7. Unknown speakers, diarisation, enrolment

- [verified] **pyannote.audio** (https://github.com/pyannote/pyannote-audio). Pipelines: `community-1` (open),
  `3.1` (legacy), `precision-2` (hosted, paid). DER, 3.1 → community-1 (benchmark updated 2025-09):

  | Set | 3.1 | community-1 |
  |---|---|---|
  | AMI IHM | 18.8 | 17.0 |
  | VoxConverse | 11.2 | 11.2 |
  | AliMeeting | 24.5 | 20.3 |
  | DIHARD3 | 21.4 | 20.2 |
  | CALLHOME | 28.5 | 26.7 |
  | AVA-AVD | 49.7 | 44.6 |

  Speed: 31-37 s per hour of audio on an **H100**. The model card for 3.1
  (https://huggingface.co/pyannote/speaker-diarization-3.1) needs an HF token and gated terms, and states no
  CPU real-time factor. [secondary] A CPU RTF of 0.86 is self-reported by a third party
  (https://pypi.org/project/diarize/). **No Jetson figure exists.** The box's free memory was a few hundred MB
  steady in the 2026-10-03 memory-pressure profile (a dated observation), so it
  is not a candidate.
- [secondary] **NVIDIA Nemotron 3 Diarization** (about 100M parameters, streaming, up to 8 speakers) is already
  on the program tracker as a B4.1 candidate (`docs/architecture/beat-the-bar-2026-program.md` B4.1), off the
  hot path.
- [secondary] **Open-set identification**: accept the best model only if the score is at or above θ,
  otherwise "unknown". There are three error types: misidentification, false accept of an unknown, and false
  reject of an enrolled speaker. Rejection gets harder as the enrolled set grows (Sivakumaran et al.,
  https://www.isca-archive.org/eurospeech_2003/sivakumaran03_eurospeech.pdf). VoxBlink2 gives the DIR@FAR
  metric with 1/3/5 enrolment utterances (https://arxiv.org/pdf/2407.11510).
- [unverified] **No source specific to clustering unknown household voices online.** The nearest is AHC/VBx
  inside community-1. Prototype it.
- [verified] **Enrolment patterns**:
  - guided: Google's explicit teaching, Alexa's about 10 phrases, Siri's 5 utterances;
  - learned: Siri's implicit enrolment (up to 40, contamination warning) and Alexa updating "over time";
  - human-confirmed: Frigate.
- [verified] **Guests**: Google gives a non-match a guest query with no personal results. Alexa gives a
  generic account user with no person object. Both serve, and neither reveals.
- [secondary] **"This is Sam"**: there is no shipped precedent for introducing a visitor. The hazards are
  Siri's contamination and Frigate's no-unlearning.
- **Zoe's own numbers** ([ResNet34 record](./speaker-resnet34-optimisation-2026-10.md)), relative evidence only
  because owner labels are inferred:
  - owner strict-core FRR 0.29-0.48 % at FAR 1 % with 5-clip enrolment and centring;
  - public far-field FRR about 5 % at FAR 1 % and about 16.7 % at FAR 0.1 %;
  - Resemblyzer misses the owner about 11 % at FAR 1 %.

  Open-set rejection of visitors at Resemblyzer's accuracy would flag the owner as a stranger roughly one
  turn in nine. **Unknown-voice noticing needs ResNet34 acting.**

## 8. Anti-spoofing

- [verified] **ASVspoof 2019** (https://arxiv.org/abs/1911.01601): logical access (TTS and voice conversion)
  and physical access (simulated replay). Some spoofs "cannot be differentiated from bona-fide utterances even
  by human subjects".
- [verified] **ASVspoof 2021** (https://arxiv.org/abs/2210.02437): LA countermeasures were robust to codecs.
  PA showed "a lack of robustness to variations between simulated and real acoustic environments". Deepfake
  detectors "lack generalization across different source datasets".
  - [secondary] Best LA EER 1.32 %; DF about 15.6-16 % at the challenge and about 5 % with later wav2vec
    systems; PA hardest (around 20 % EER, not confirmed from the table).
- [verified] **ASVspoof 5** (https://arxiv.org/abs/2408.08739, 2024-08-16): crowdsourced data, adversarial
  attacks "for the first time", "Attacks significantly compromise the baseline systems".
  - [secondary] Baselines minDCF above 0.7 and EER above 29 %. The best closed track-1 system had EER 8.61 %
    but an actual DCF of about 0.996 (poorly calibrated).
  - [verified] The post-challenge overview (https://arxiv.org/abs/2601.03944, 2026-01-07, 53 teams) says
    performance "degrades under adversarial attacks" and with neural codecs.
- [verified] **AASIST** (https://arxiv.org/abs/2110.01200; https://github.com/clovaai/aasist): EER 0.83 % on
  ASVspoof 2019 LA; AASIST-L has 85k parameters. This is in-domain, and the 2021 and 5 results show it does
  not transfer.
- [secondary] **Real-world bypass**:
  - an AI-cloned voice from about 5 minutes of audio passed a UK bank's voice check
    (https://www.vice.com/en/article/dy7axa/how-i-broke-into-a-bank-account-with-an-ai-generated-voice, 2023);
  - the US Senate Banking Committee wrote to banks
    (https://www.banking.senate.gov/imo/media/doc/charles_schwab_voice_authentication_letter_62061.pdf).
- [secondary] **ISO/IEC 30107-3**: APCER is attacks accepted, BPCER is genuine users rejected. iBeta defines
  Levels 1 and 2 (https://ibeta.com/wp-content/uploads/2022/06/iBeta-Biometrics-ISO-PAD-and-FIDO-Comparison.pdf).
- **What a home assistant realistically needs** [inference]:
  - The threats are a recording of a family member played from a phone, a clone from social-media clips, a
    sibling who sounds alike, and a photo held to the camera.
  - Proportionate defence:
    - voice and face never reach the floors;
    - a PIN for step-up;
    - the phone for money;
    - face liveness (cheap, already chosen);
    - a different voice closes the session;
    - the lab cells in the design (§7) measure replay and clone acceptance rather than assume them.
  - A speech anti-spoofing model is not worth its RAM and false-reject cost here.

## 9. Privacy and law (not legal advice; every item below should go to a lawyer)

**Australia, Commonwealth**

- [verified] **Biometrics are sensitive information.** OAIC APP Guidelines Ch B, B.141: "biometric information
  that is to be used for the purpose of automated biometric verification or biometric identification, or ...
  biometric templates (s 6(1))".
  https://www.oaic.gov.au/privacy/australian-privacy-principles/australian-privacy-principles-guidelines/chapter-b-key-concepts.
  The Act text itself was not readable.
- [verified] **OAIC facial recognition guide** (2024-11-19, updated 2026-07-29):
  - signage alone "will not generally be sufficient";
  - "implied consent should not be relied on when collecting sensitive information".
  https://www.oaic.gov.au/privacy/privacy-guidance-for-organisations-and-government-agencies/organisations/facial-recognition-technology-a-guide-to-assessing-the-privacy-risks
- [verified] **Bunnings** (OAIC, 2024-11-19): "consent is generally required"
  (https://www.oaic.gov.au/news/media-centre/bunnings-breached-australians-privacy-with-facial-recognition-tool).
  - [secondary] The Administrative Review Tribunal ([2026] ARTA 130, 2026-02-04) overturned the consent breach
    on an unlawful-activity exception, but kept the transparency findings, and held that about 0.004 s of
    data was still a "collection"
    (https://www.gtlaw.com.au/insights/administrative-review-tribunal-upholds-bunnings-use-of-facial-recognition-technology).
- [secondary] **Kmart** (2025 AICmr 155, 2025-08-26): breaches of APP 3.3, 5.1 and 1.3; the collection was
  "disproportionate"
  (https://www.oaic.gov.au/__data/assets/pdf_file/0029/256583/Commissioner-Initiated-Investigation-into-Kmart-Australia-Limited-Privacy-2025-AICmr-155-26-August-2025.pdf).
- [secondary] **Household exemption, s 16**: the APPs do not apply to an individual's handling of personal
  information "only for the purposes of ... his or her personal, family or household affairs" (search-result
  paraphrase of https://20.austlii.edu.au/au/legis/cth/consol_act/pa1988108/s16.html; re-read before relying
  on it).
  - **Preliminary, unresolved reading for counsel:** does it cover an owner running Zoe for their household?
    Does it reach anyone *supplying* Zoe to other households (on its face it speaks of an individual's own
    affairs)? It says nothing about visitors' rights under other laws.
- [verified] **Small business exemption**: turnover of A$3 m or less, with exceptions (s 6D), OAIC Ch B.
  [secondary] It is still in force; the Tranche 2 draft reportedly omits its removal (HSF Kramer, unread).
- [secondary] **Statutory tort for serious invasions of privacy**: Privacy and Other Legislation Amendment
  Act 2024, Sch 2, commenced about 10 June 2025. Intentional or reckless intrusion on seclusion or misuse of
  information; actionable without proof of damage
  (https://corrs.com.au/insights/the-new-statutory-tort-for-serious-invasions-of-privacy-and-its-implications-for-business).
  - **Lawyer:** this is the relevant exposure for an always-listening device in a home that visitors enter,
    regardless of the APP exemptions.
- [secondary] **Children's Online Privacy Code**: exposure draft 2026-03-31, registration due by 2026-12-10;
  it targets social media and online services, not a private home assistant as drafted
  (https://www.oaic.gov.au/news/media-centre/oaic-releases-exposure-draft-of-the-childrens-online-privacy-code).
- [secondary] **Tranche 2 draft** (Privacy Amendment (Personal Data Protection) Bill 2026, released
  2026-08-31): a fair-and-reasonable test, a right to erasure, a controller/processor framework
  (https://www.claytonutz.com/insights/2026/september/the-next-wave-of-australian-privacy-reform-key-proposals-in-the-draft-personal-data-protection-bill-2026).

**Western Australia**

- [secondary] The **Privacy and Responsible Information Sharing Act 2024** covers WA public entities and their
  contractors (most obligations from 2026-07-01). Private organisations are "generally not covered"
  (https://hwlebsworth.com.au/expris-delivery-timetable-set-for-the-commencement-of-was-new-privacy-laws/).
- [verified] **Surveillance Devices Act 1998 (WA)**
  (https://legislation.wa.gov.au/legislation/prod/filestore.nsf/FileURL/mrdoc_27928.htm/$FILE/Surveillance%20Devices%20Act%201998%20-%20[02-f0-03].html):
  - s 5(1) forbids using a listening device "to record a private conversation to which that person is a
    party";
  - penalty $5,000 or 12 months' imprisonment for an individual, $50,000 for a body corporate;
  - s 5(3)(c) makes it lawful where "each principal party ... consents expressly or impliedly";
  - a "private conversation" excludes circumstances where the parties "ought reasonably to expect that the
    conversation may be overheard".
  - **Lawyer:** does Zoe's capture of a visitor's speech count as recording? Is a spoken introduction plus
    the visitor's "yes" express consent? Does a visible "listening" indicator make it reasonably expected?

**EU / UK, for comparison**

- [verified] **GDPR Art 9(1)** prohibits processing "biometric data for the purpose of uniquely identifying a
  natural person", unless an exception applies such as Art 9(2)(a) "explicit consent"
  (https://www.legislation.gov.uk/eur/2016/679/article/9/adopted).
  - **Art 2(2)(c)** excludes processing "by a natural person in the course of a purely personal or household
    activity" (https://www.legislation.gov.uk/eur/2016/679/article/2/adopted).
  - **Preliminary, unresolved reading for counsel:** whether a household running Zoe, and in particular
    holding a visitor's voiceprint, falls inside that exemption is not settled here. The two sources below
    point in different directions, and nothing in this record decides it.
  - Recital 18 keeps the Regulation applying to the controllers or processors that provide the means. Recital
    51 makes photographs biometric data only when processed "through a specific technical means"
    (https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679).
  - [secondary] Belgian DPA: the household exemption for biometrics applies under strict conditions, such as
    the data being usable only by the data subject
    (https://www.wsgrdataadvisor.com/2022/01/belgian-data-protection-authority-clarifies-key-rules-on-biometric-data-processing/).
- [verified] **EU AI Act Art 5** (https://artificialintelligenceact.eu/article/5/) bans:
  - emotion inference in workplaces and education;
  - biometric categorisation for sensitive traits;
  - real-time remote biometric identification "in publicly accessible spaces" for law enforcement.

  **Preliminary, unresolved reading for counsel:** these prohibitions are framed around workplaces, education
  and publicly accessible spaces, and a private home may fall outside them. That is not established here: Art 5
  is not the whole Act, and other obligations may attach to a product placed on the EU market. The design
  avoids the adjacent area anyway (no emotion or sensitive-trait inference from biometrics), consistent with
  the emotional-safety note.
- [secondary] **UK ICO Age Appropriate Design Code**: high privacy by default, profiling off by default,
  minimisation. Nothing biometric-specific was found.

**US, for comparison**

- [secondary] **Illinois BIPA** (740 ILCS 14; statute unreachable; https://lig.ilga.gov/legislation/ilcs/documents/074000140K10.htm):
  - "voiceprint" and "scan of ... face geometry" are biometric identifiers;
  - it requires a public written retention and destruction policy and a **written release** before
    collection;
  - damages are $1,000 negligent or $5,000 intentional/reckless;
  - SB 2979 (signed 2024-08-02) makes repeated collection one violation per person and allows electronic
    consent (https://www.fisherphillips.com/en/insights/insights/breaking-bipa-news-illinois-curtails-massive-damage-awards-in-biometric-cases).
  - **Preliminary, unresolved reading for counsel:** BIPA appears aimed at private entities collecting from
    Illinois residents, so it may matter only if Zoe were ever offered to or used by them. When, and to whom, it
    applies is not settled here.
- [secondary] **COPPA Rule amendments** (published 2025-04-22, compliance 2026-04-22): biometric identifiers
  used for recognition (voiceprints, faceprints) are personal information
  (https://www.hunton.com/privacy-and-cybersecurity-law-blog/ftc-publishes-final-coppa-rule-amendments).

**Visitors and children: the honest position**

- No authority was found that addresses a private household storing a non-consenting visitor's voiceprint.
- Every anchor (OAIC consent-not-implied, the WA principal-party consent, GDPR explicit consent, BIPA written
  release) points the same way: **do not keep a visitor's voiceprint without that visitor's own express
  consent**, keep nothing at all from un-introduced voices, and be stricter still for children (COPPA treats
  their voiceprints as personal information; the OAIC children's code is coming).
- The design follows that: ephemeral clusters, self-consent by voice, expiry, no child visitor voiceprints.

**Items for a lawyer** (owner decision 11 in the design):

1. The s 16 household exemption, for the owner and for any distribution.
2. WA Surveillance Devices Act s 5 and continuous listening that captures visitors.
3. The 2025 statutory tort.
4. Whether a spoken "yes" is adequate express consent for a visitor voiceprint, and for recording.
5. Children: parental consent on a child's behalf, and visiting children.
6. If Zoe is ever offered to others: APP obligations as a supplier, small-business status, Tranche 2, and
   BIPA, COPPA and GDPR abroad.

## 10. What Zoe has today (code survey, file:line)

A sub-agent surveyed this worktree (origin/main `b02a04a7b`). I spot-checked the PIN constants, the role
canonicalisation, the introduce intent, the voice-session TTL and the presence window against the source.
`ZP` = `services/zoe-data`.

**Authentication**

- **Panel PIN.**
  - `ZOE_PIN_MAX_ATTEMPTS` 5, `ZOE_PIN_LOCKOUT_S` 300, `ZOE_PIN_CHALLENGE_TTL_S` 120 (`ZP/routers/panel_auth.py:36-38`).
  - The attempt counter is in memory (`:45-73`).
  - Challenge `POST /api/panels/auth/challenge` (`:541-565`); `create_pin_challenge_internal` (`:568-610`).
  - The PIN route (`:613-916`) ignores a caller `user_id`. It resolves the challenge's user, then a validated
    bound session, then the panel default (`:110-158,670-677`), and delegates to zoe-auth
    `/api/auth/login/passcode` (`:746-761`).
  - On success it upserts `ui_panel_sessions` (`:856-866`) and sets `_VOICE_SESSIONS[panel]["bound_user_id"]`
    (`:850-852`) [src].
- **Passcodes** are argon2 (t=2, 64 MB, p=1), 4-8 digits, with a 32-pattern blocklist
  (`services/zoe-auth/core/passcode.py:30-58`). Passwords are bcrypt (`core/auth.py:93,183`).
  - Session TTLs: STANDARD 480 min, PASSCODE 60, GUEST 30, API 1440; remember-me 30 days
    (`core/sessions.py:57-96,341-353`).
  - Throttling: per-IP backoff and a hard block per (IP, user), never user-global
    (`core/security.py:89-100`) [src].
- **Session freshness** = `ui_panel_sessions.last_seen_at` within the idle-logout window
  (`system_preferences.panel_idle_logout_s`, else `ZOE_PANEL_SESSION_TRUST_WINDOW_S` 900; `ZP/routers/voice_tts.py:2069-2174`).
  - Set through `GET/PUT /api/system/panel/idle-logout` (`ZP/routers/system.py:2512-2537`).
  - The touch options are 15 min-12 h (`services/zoe-ui/dist/touch/home.html:3897-3908`).
  - Guest reclaim after 300 s of owner silence (`ZP/routers/ui_actions.py:33,46-68`) [src].
- **Roles.**
  - zoe-auth has `admin`, `user`, `family_member`, `child`, `guest` (`services/zoe-auth/models/database.py:18-23`).
  - zoe-data's capability matrix has `admin`, `user`, `guest` only (`ZP/guest_policy.py:127`). `_canonical_role`
    sends every other role, **including `family_member` and `child`, to the guest matrix**
    (`:213-218`, used by `get_matrix_for_role` `:221-223`), while `is_guest_user` (`:262-268`) treats them as
    non-guests.
  - Child and adult role sets exist only in `ZP/manner_block.py:54-57` and `ZP/enrollment_state.py:221-240`.
  - [src; spot-checked; recorded in `docs/knowledge/open-problems.md`]
- **Voice scope and restraint.**
  - `ZP/voice_scope.py` classifies a turn as public or user-scoped. A scoped turn with no identity raises a PIN
    challenge (`ZP/routers/voice_tts.py:1905,4278-4288`).
  - Restraint (`ZP/restraint.py:40-72`, `ZOE_RESTRAINT` default shadow) withholds the sensitive classes
    (health, money, family_conflict, grief, other_member, affect) when the speaker verdict is False [src].

**Voice**

- **Panel side.**
  - Implemented, **disabled by default**: `SPEAKER_ID_ENABLED` defaults false, and `_speaker_claim_for_turn`
    returns early, so nothing is scored. When it is enabled with `SPEAKER_ID_SHADOW` on (its default), a claim
    is scored and logged to `~/.zoe-voice/speaker_shadow_metrics.jsonl` but never attached
    (`scripts/setup/zoe_voice_daemon.py:408-428,2655-2706`).
  - Profiles sync every 3600 s (`:2236`).
- **Server side.**
  - `ZOE_SPEAKER_ID_THRESHOLD` code default 0.82 (`ZP/routers/voice_tts.py:6474-6479`).
  - Claim acceptance is device-token only, needs a score at or above the threshold (`:6527-6554`) and a
    consent row (`:6557-6574`).
  - The tri-state verdict (`:6495-6524`) feeds memory provenance, restraint and the voice session.
  - Routes: enrol `:6590`, identify `:6708`, sync `:6803`, list `:6845`, delete `:6889`.
  - Storage: `speaker_profiles` (0001, plus 0023 consent, plus 0048 adaptation) [src].
- **Voice-verified session** (`ZP/voice_session.py`, `ZOE_VOICE_SESSION` off):
  - TTL 120 s, clamp 10-900 (`:71-73`);
  - a model gate (`:99-117`) needs the ResNet34 id, with Resemblyzer only by override;
  - `observe_turn` (`:381-446`); closes on consent revoke or profile delete (`:449-470`);
  - the step-up policy is in `ZP/lexicons_data/voice_session_policy.json` [src].
  - Note: `routers/voice_tts.py:140` has a separate `_VOICE_SESSION_TTL_S = 300`, which is the voice
    *conversation* session, not the verified session.
- **Identity freshness (#2026).** A member identity comes only from this turn, a fresh PIN session, the
  caller's own session, or an open voice session (`ZP/routers/voice_tts.py:3451-3600`; `ZP/routers/AGENTS.md`;
  `ZP/tests/test_voice_identity_freshness.py`) [src].
- **Adaptation** (`ZP/speaker_adaptation.py`, `ZOE_SPEAKER_ADAPT` off):
  - guards are score 0.85, anchor 0.80, at least 2 s, margin 0.10, rate limits and a drift bound;
  - corroboration needs a bound panel, a PIN within 900 s or a face score of 0.60 or more (`:331-362`) [src].
  - [branch] #2024 retunes these for ResNet34 (0.342 / anchor 0.45).
- **ResNet34 switch** [branch, PR #2024 open]:
  - `ZOE_SPEAKER_MODEL=resemblyzer|dual|resnet34`, with a `model_id` per profile (migration 0049);
  - thresholds 0.233 / 0.265 at FAR 1 % and 0.309 / 0.351 at FAR 0.1 % (short / long clips);
  - 5-clip enrolment;
  - on the Pi: p50 about 70 ms, p95 about 220 ms, standalone peak 122 MB.
- **Zoe-driven enrolment** (`ZOE_ENROLL_OFFER` off, kids off; `ZP/voice_enrollment.py`,
  `ZP/enrollment_state.py`, `ZP/routers/enrollment.py`, migration 0046) [src].

**Face**

- `scripts/setup/zoe_face_id.py`:
  - SCRFD plus MobileFaceNet 512-d;
  - `FACE_ID_ENABLED` false (`:39`);
  - `FACE_MIN_PX` code default 60 (`:47`), overridable by the panel's environment;
  - the camera is opened per call under a lock (`:369-398`);
  - **`identify_face` (`:401-434`) is called only by `scripts/setup/zoe_enroll_flow.py`**, so no runtime
    producer exists.
- Server `ZP/routers/face_id.py`: flag-gated by `ZOE_FACE_ID_ENABLED` (`:48-62`), threshold 0.45
  (`:55-57`), up to 8 poses, consent NOT NULL.
- USB and power notes are at `scripts/setup/zoe_enroll_flow.py:27-31,121-146` [src].

**Presence**

- `ZP/proactive/presence.py` `panel_presence_tier`: owner / bound_guest / absent, window
  `ZOE_PRESENCE_WINDOW_S` 900 (`:28-100`).
- The kiosk heartbeat sends `is_foreground: true` always (`services/zoe-ui/dist/js/touch-ui-executor.js:674,690`).
- The 3-minute sleep card is at `touch/home.html:1234-1303`.
- The sleep gate reads room toggles only; Zoe does not currently consume any presence or occupancy sensor (`ZP/routers/panel_config.py:382-416`).
- `panels.last_seen_at` is read but never written (`ZP/routers/panel_auth.py:251,285,327`).
- The orphaned `touch/js/presence-detection.js` uses a simulated PIR.
- [src]

**Bluetooth and Omi**

- omi-sync connects to the pendant with `hcitool lecc` (no scan): scan interval 30 s, cooldown 120 s, the
  roughly 2-minute cadence (`services/omi-sync/omi_sync/config.py:91-93`, `runner.py:86-92`).
- Nothing records "pendant seen". The device registry is `ZP/omi_devices.py` (migration 0047).
- panel-bluetooth reads RSSI for the settings list only (`ZP/bluetooth_broker.py:260-272`).
- One shared radio flock is used (`ZOE_BLE_RADIO_LOCK`) [src].

**Home Assistant**

- No presence consumer exists. `homeassistant/configuration.yaml` loads `default_config` plus OIDC with
  `automatic_person_creation`.
- Entities are reachable via `GET /api/ha/entities?domain=` (`ZP/routers/ha_control.py:72-90`).
- Zoe does not currently consume any HA presence entity (`person`, `device_tracker`, `binary_sensor`) [src].

**People graph**

- `people` (`ZP/alembic/versions/0001_initial_schema.py:87-102`) plus 0007 (`is_partial`,
  `introduced_by_person_id`, `how_we_met`, `first_met_date`, `person_relationships`), 0015 (temporal edges),
  0037 (authority, origin) and 0043 (evidence).
- `is_partial` stubs are hidden from lists and the recall dossier (`ZP/person_extractor.py:301-317`,
  `ZP/routers/people.py:209`).
- The `people_introduce` intent: `_handle_introduce_intent` (`ZP/routers/voice_tts.py:3099-3139`) creates or
  finds a person; `_INTRO_STATE` (`:149`, set `:4301`) asks follow-ups.
- **Nothing links a person to a voice or a face.**
- [src]

**Settings UI**

- Desktop `services/zoe-ui/dist/settings.html`: nav at `:579-600`; Password, PIN (`:730`), Sessions, Speaker
  Identity (`:808-832`, enrol with a consent box, list, delete; code at `:1441-1606`).
- Touch estate `touch/home.html` `wireSettings` (`:3809-3926`): signed-in name, idle logout, switch user,
  sign out.
- The legacy `touch/settings.html` security section is at `:829-878`.
- **No face UI and no security preferences anywhere.**
- Backing stores: `user_preferences`, `system_preferences`, `display_preferences` [src].

**Gaps** (what the design builds):

- no evidence ledger;
- no fusion or decay;
- touch, wake and VAD not recorded;
- no proximity source;
- no unknown-voice handling;
- no per-user security preference;
- no child capability tier;
- `ambient_memory` has no retention sweep.

## 11. Could not verify (carried forward)

- The exact text of the Privacy Act s 6 and s 16.
- The BIPA statute.
- A BLE room-level accuracy figure.
- pyannote speed on a Jetson.
- Whether HomePod Personal Requests need the iPhone nearby.
- Android's 2024 Smart Lock changes.
- The official Amazon Voice ID text.
- CTAP 2.2 §11.5.
- The exact ASVspoof 2021 PA table numbers.
- Whether BOSARIS-style calibration needs more labelled household data than one shadow fortnight provides.
  The design states 0 bits until calibrated for exactly this reason.
