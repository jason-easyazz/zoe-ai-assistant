"""Twenty INVENTED panel-style sessions for the panel-extraction split lab.

Everything here is fiction written for this lab: the Quillfeather household of Marrowbank does not exist, and no line is
a real utterance. The speaker is the member "Bram"; the others are his partner Odalys, his daughter Tamsin, his brother
Corvin and the dog Pickle. Turns are short, the way a wall panel hears them (one breath, a command plus maybe a clause),
written in the sentence-case-with-punctuation shape a streaming ASR emits; `run_split.py` also feeds a lower-case,
unpunctuated variant to the deterministic gates.

Groups (8 / 6 / 6):
  D*  durable facts stated in passing         -> a good extractor should produce every `expect` item
  O*  oblique / implicit facts                -> a good extractor should produce them, but they are inferential
  C*  pure commands, nothing worth keeping    -> a good extractor returns []

`expect` items: `fact` is the reference statement; `must` is the lower-case token set a returned fact must contain (all
of them) to count as a hit. Scoring is deliberately lenient on wording and strict on content.
"""
from __future__ import annotations

SPEAKER = "member-synth-bram"

A = "assistant"
U = "user"

SESSIONS: list[dict] = [
    # ---- D: durable facts in passing --------------------------------------------------------------------------------
    {"id": "D1", "group": "durable", "turns": [
        (U, "Find me a quick pesto recipe."), (A, "Here's a basil pesto that takes ten minutes."),
        (U, "Not that one, I'm allergic to cashews."), (A, "Got it, here's one with pine nuts instead."),
        (U, "Set a timer for twelve minutes."), (A, "Timer set for twelve minutes.")],
     "expect": [{"fact": "User is allergic to cashews.", "must": ["allerg", "cashew"]}]},
    {"id": "D2", "group": "durable", "turns": [
        (U, "Play something calm."), (A, "Playing a calm mix."),
        (U, "My daughter Tamsin turns eight on the fourteenth of March."), (A, "Lovely, that's coming up."),
        (U, "Turn it down a bit."), (A, "Turned down.")],
     "expect": [{"fact": "User's daughter Tamsin turns eight on 14 March.", "must": ["tamsin", "march"]}]},
    {"id": "D3", "group": "durable", "turns": [
        (U, "What's the weather tomorrow?"), (A, "Sunny, top of twenty-four."),
        (U, "Good, I start my new job at Fernhollow Joinery on Monday."), (A, "Congratulations!"),
        (U, "Set an alarm for six thirty."), (A, "Alarm set for six thirty.")],
     "expect": [{"fact": "User is starting a new job at Fernhollow Joinery.", "must": ["fernhollow"]}]},
    {"id": "D4", "group": "durable", "turns": [
        (U, "Add oat milk to the shopping list."), (A, "Added oat milk."),
        (U, "I'm vegan now, so no more cow's milk."), (A, "Noted."),
        (U, "And bin bags."), (A, "Added bin bags.")],
     "expect": [{"fact": "User is vegan.", "must": ["vegan"]}]},
    {"id": "D5", "group": "durable", "turns": [
        (U, "Turn off the lounge lights."), (A, "Lounge lights off."),
        (U, "My brother Corvin lives in Marrowbank."), (A, "Okay."),
        (U, "How far is that from here?"), (A, "About forty minutes by car.")],
     "expect": [{"fact": "User's brother Corvin lives in Marrowbank.", "must": ["corvin", "marrowbank"]}]},
    {"id": "D6", "group": "durable", "turns": [
        (U, "Remind me at six to feed Pickle."), (A, "I'll remind you at six."),
        (U, "Pickle's our dog, she's a kelpie."), (A, "A kelpie, nice.")],
     "expect": [{"fact": "User has a dog named Pickle, a kelpie.", "must": ["pickle", "kelpie"]}]},
    {"id": "D7", "group": "durable", "turns": [
        (U, "What time is it?"), (A, "It's ten past five."),
        (U, "I play five-a-side football every Thursday night."), (A, "Sounds fun."),
        (U, "So remind me to pack my boots."), (A, "Reminder set.")],
     "expect": [{"fact": "User plays five-a-side football every Thursday night.", "must": ["football", "thursday"]}]},
    {"id": "D8", "group": "durable", "turns": [
        (U, "Keep it quiet tonight."), (A, "Okay."),
        (U, "My partner Odalys works night shifts at the hospital."), (A, "Understood."),
        (U, "Turn the volume down."), (A, "Volume down.")],
     "expect": [{"fact": "User's partner Odalys works night shifts at the hospital.", "must": ["odalys", "night"]}]},
    # ---- O: oblique / implicit --------------------------------------------------------------------------------------
    {"id": "O1", "group": "oblique", "turns": [
        (U, "Find a gluten free bread recipe."), (A, "Here's a simple one."),
        (U, "Ever since the coeliac diagnosis I can't touch normal bread."), (A, "That's a big change.")],
     "expect": [{"fact": "User has coeliac disease.", "must": ["coeliac"]}]},
    {"id": "O2", "group": "oblique", "turns": [
        (U, "How long to drive to Marrowbank?"), (A, "About forty minutes."),
        (U, "Going to see Mum for her birthday on Saturday."), (A, "Have a lovely time.")],
     "expect": [{"fact": "User's mum's birthday is on Saturday.", "must": ["birthday"]}]},
    {"id": "O3", "group": "oblique", "turns": [
        (U, "Set an alarm for four thirty."), (A, "Alarm set for four thirty."),
        (U, "Early start at the bakery again."), (A, "Good luck.")],
     "expect": [{"fact": "User works early shifts at a bakery.", "must": ["bakery"]}]},
    {"id": "O4", "group": "oblique", "turns": [
        (U, "Play the rain sounds for Tamsin."), (A, "Playing rain sounds."),
        (U, "She can't get to sleep without them."), (A, "Sweet dreams to her.")],
     "expect": [{"fact": "Tamsin needs rain sounds to fall asleep.", "must": ["tamsin", "rain"]}]},
    {"id": "O5", "group": "oblique", "turns": [
        (U, "Remind me to do my physio stretches at eight."), (A, "Reminder set for eight."),
        (U, "The knee's been bad since the surgery."), (A, "Sorry to hear that.")],
     "expect": [{"fact": "User had knee surgery and does physio.", "must": ["knee"]}]},
    {"id": "O6", "group": "oblique", "turns": [
        (U, "Corvin rang and said, \"I'm moving to Halvercombe next month.\""), (A, "Big news."),
        (U, "Remind me to call him Sunday."), (A, "Reminder set for Sunday.")],
     "expect": [{"fact": "User's brother Corvin is moving to Halvercombe next month.", "must": ["corvin", "halvercombe"]}],
     # ground truth for the wrong-owner check: these words are Corvin's, not the speaker's
     "foreign_spans": ["I'm moving to Halvercombe next month."]},
    # ---- C: pure commands -------------------------------------------------------------------------------------------
    {"id": "C1", "group": "command", "turns": [
        (U, "Turn on the kitchen light."), (A, "Kitchen light on."), (U, "Brighter."), (A, "Done.")], "expect": []},
    {"id": "C2", "group": "command", "turns": [
        (U, "Set a timer for ten minutes."), (A, "Ten minutes, starting now."), (U, "Cancel that."), (A, "Cancelled.")],
     "expect": []},
    {"id": "C3", "group": "command", "turns": [
        (U, "What's the weather?"), (A, "Cloudy, eighteen degrees."), (U, "And tomorrow?"), (A, "Rain in the afternoon.")],
     "expect": []},
    {"id": "C4", "group": "command", "turns": [
        (U, "Play some jazz."), (A, "Playing jazz."), (U, "Skip."), (A, "Skipped."), (U, "Stop."), (A, "Stopped.")],
     "expect": []},
    {"id": "C5", "group": "command", "turns": [
        (U, "Add bin bags to the shopping list."), (A, "Added."), (U, "What's on it?"), (A, "Oat milk and bin bags.")],
     "expect": []},
    {"id": "C6", "group": "command", "turns": [
        (U, "What time is it?"), (A, "Quarter to nine."), (U, "Goodnight."), (A, "Goodnight.")], "expect": []},
]
