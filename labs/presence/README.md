# Presence-fusion lab

Measures `services/zoe-data/presence_fusion.py` on synthetic multi-day household traces with ground truth, and shows
which sensor buys the most. Stdlib only: no model, no network, no service, nothing live is read. Record:
[docs/knowledge/presence-fusion.md](../../docs/knowledge/presence-fusion.md).

```bash
python3 labs/presence/harness.py                      # 11 scenarios x 3 seeds x 28 days
python3 labs/presence/harness.py --sweep              # + the left dwell / leaving-context sweep on the phone scenario
python3 labs/presence/harness.py --days 7 --seeds 1 --only today,phone
python3 -m pytest -q labs/presence/test_lab.py        # the lab's own checks (not in CI, by the labs contract)
```

## The household (`household.py`, placeholder people)

- `adult_a`: office job on weekdays (leaves about 07:45, back about 17:30), out on Saturday morning, wears the pendant,
  owns the PIN and the weekday panel alarm, **forgets the phone at home once a week**.
- `adult_b`: the school run with `child_c` every weekday; three days a week out until school pick-up and home **together
  with the child** (two people arriving at once).
- `child_c`: school on weekdays, no phone.
- `guest_g`: one evening a week in the living room; never registered.
- One panel, in the kitchen. A TV on 19:00-21:30 makes false wakes at the panel; one in a hundred is falsely accepted as
  an adult (a deliberately pessimistic FAR).
- Phones sleep their Wi-Fi: drop-outs of 4-30 min at night and 3-12 min by day; the HA tracker reports away 3 min after
  the phone leaves the network.
- Every random draw is made once per seed, so scenarios differ only in which observation families they keep.

## Scenarios

`today` = the signals that exist with no new hardware and no new config (verified voice, unverified voice, PIN, member
touch, alarm, pendant sync, typed chat, room toggles, TV noise). `phone` = today + the HA phone tracker (operator
configuration, no hardware). `+door`, `+ble`, `+mmwave`, `+camera` (person-detect and face match in the kitchen and
living room) simulate each hardware adapter present.

## What the numbers mean

Sampled every 5 min per tracked person. `home P/R`, `away P/R` = precision / recall of each state against the truth
(unknown counts against recall, not precision). `left found` = true departures matched by a `left` event within 90 min;
`left delay` = median minutes from the real departure to the event; `false left /person-day` = `left` events matching no
departure. The target is **at most 0.03 false `left` per person-day** (about one a month per person). `gate` = the
privacy gate at the kitchen panel while `adult_a` is there: how often `others_present` said yes when they were alone, and
when someone else was there.
