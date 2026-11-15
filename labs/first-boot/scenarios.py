"""Scripted people for the first-boot simulator. Synthetic only: no real names, words or households.

``answers[step]`` is the queue of BEHAVIOURS the person gives each time that step is offered (then "yes"); ``_after_park`` is
whether anyone comes back after a pause ("gone" = nobody does); ``seed`` is SQL run on the blank store first;
``power_loss_after`` lists steps after whose success the unit loses power (a full restart: only the store survives).
"""

SCENARIOS = (
    {"name": "ideal", "answers": {}},
    {"name": "says_later", "answers": {
        "panel_named": ["later"], "owner_voice": ["no"], "phone_linked": ["later"], "backup_target": ["later"],
        "more_members": ["no"], "calendar": ["later"], "music": ["later"]}},
    {"name": "silences", "answers": {
        "owner_account": ["silence", "silence"], "owner_pin": ["silence", "silence", "silence"], "first_win": ["silence"]}},
    {"name": "mistakes", "answers": {
        "panel_named": ["unclear", "unclear"], "time_and_place": ["no"], "owner_pin": ["fail"],
        "owner_voice": ["fail", "fail"], "phone_linked": ["fail"]}},
    {"name": "child_answers", "answers": {
        "owner_account": ["child"], "owner_voice": ["child"], "owner_pin": ["child"]},
     "seed": ["INSERT INTO auth_users VALUES ('child-sim', 'child', 'child', NULL, '2026-11-14 08:00:00', 1)"]},
    {"name": "walks_away", "answers": {"owner_voice": ["walk_away"], "_after_park": ["yes"]}},
    {"name": "walks_away_for_good", "answers": {"owner_voice": ["walk_away"], "_after_park": ["gone"]}},
    {"name": "power_loss", "answers": {}, "power_loss_after": ["panel_paired", "owner_voice"]},
    {"name": "owner_refuses_first", "answers": {"owner_account": ["later", "no", "later"]}},
)
