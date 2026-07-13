"""Label set for the setfit-router spike.

12 tool domains + the critical `chat` none-class. Derived from
services/zoe-data/semantic_router.py ROUTES (8 tool domains + chat) plus the
domains the live ROUTES is missing today: notes, journal, music, smart_home
(all present in the needle-benchmark eval corpus and in Zoe's real tool set).
"""

LABELS = [
    "calendar",
    "lists",
    "reminders",
    "timers",
    "weather",
    "time",
    "people",
    "memory",
    "notes",
    "journal",
    "music",
    "smart_home",
    "chat",  # none-class: conversational turns must NOT route to tools
]

# Seed utterances per class. calendar..chat seeds are copied verbatim from the
# live semantic_router.ROUTES (prod ground truth); notes/journal/music/
# smart_home are hand-seeded from intent_router regex phrasing + real tool
# descriptions in labs/needle-benchmark/zoe_tools.json.
SEEDS: dict[str, list[str]] = {
    "calendar": [
        "what's on my calendar today", "what's on my calendar tomorrow",
        "do I have anything on this week", "schedule a meeting for friday at 3",
        "add a dentist appointment next tuesday", "am I free on saturday",
        "what appointments do I have", "book me in for a haircut next week",
        "put lunch with sarah on my calendar monday",
    ],
    "lists": [
        "add milk to my shopping list", "put bread on the grocery list",
        "what's on my shopping list", "show me my todo list",
        "add eggs and butter to the list", "create a new packing list",
        "is milk on my shopping list", "remove bananas from my list",
        "what do I still need to buy",
    ],
    "reminders": [
        "remind me to call mum at 6", "set a reminder to take the bins out",
        "remind me to water the plants tomorrow", "what reminders do I have",
        "remind me about the meeting", "nudge me to ring the dentist later",
        "don't let me forget to pay the rent",
    ],
    "timers": [
        "set a timer for 10 minutes", "start a 5 minute timer",
        "set a timer for the pasta", "how long left on my timer",
        "cancel the timer", "give me ten minutes on the timer",
    ],
    "weather": [
        "what's the weather like", "is it going to rain today",
        "what's the temperature outside", "weather forecast for the weekend",
        "do I need an umbrella", "how hot is it in perth", "will it be sunny this arvo",
    ],
    "time": [
        "what time is it", "what's the date today", "what day is it",
        "what's today's date", "is it morning or afternoon", "got the time on you",
    ],
    "people": [
        "when is john's birthday", "what's sarah's phone number",
        "tell me about my brother", "who is michael",
        "what do I know about emma", "what's my wife's favourite colour",
        "what is my mum's name", "what's my dad's name",
        "my mum's name is janice", "my dad's name is neil",
        "my brother is called tom", "my wife's name is sarah",
        "my friend's birthday is in june", "remember that my friend lives in perth",
        "let me tell you about my friend", "I want to tell you about my mum",
        "her name is emma and she's my sister", "his birthday is the third of may",
        "my mum's birthday is the 17th of the 11th 1947",
        "my dad's birthday is the 5th of the 6th 1950",
        "her birthday is the 22nd of the 9th", "his anniversary is the 10th of the 6th",
    ],
    "memory": [
        "what did I say about the project", "do you remember what I told you yesterday",
        "what did I tell you about my goals", "remind me what we discussed",
        "what's my favourite restaurant", "have I mentioned my car before",
        "remember that I parked on level three", "I want you to remember something",
        "make a note that the wifi password is bluebird", "keep in mind I'm allergic to nuts",
        "remember I like my coffee black",
    ],
    "notes": [
        "take a note about the meeting", "create a note called project ideas",
        "write this down for me", "start a new note",
        "find my note about the car service", "search my notes for the recipe",
        "what does my note about the plumber say", "jot down that the gate code is 4412",
    ],
    "journal": [
        "add a journal entry for today", "write in my journal",
        "journal that today was a good day", "log today in my diary",
        "make a diary entry about the trip", "record how I'm feeling in my journal",
    ],
    "music": [
        "play some music", "play jazz in the kitchen", "put on my chill playlist",
        "skip this song", "next track please", "pause the music",
        "turn the music down", "resume playback", "what song is this",
        "play the beatles in the living room",
    ],
    "smart_home": [
        "turn on the lights", "turn off the kitchen lights",
        "dim the bedroom lights", "switch off all the lights",
        "brighten the living room", "lights off please",
        "turn on the fan", "is the front door locked",
        "set the thermostat to 22", "turn off the heater",
    ],
    "chat": [
        "how are you feeling today", "tell me a joke", "what's the meaning of life",
        "I'm feeling a bit down today", "what do you think about space",
        "let's have a chat", "good morning zoe", "thank you so much",
        "what is the capital of japan", "tell me a fun fact about the ocean",
        "I was just testing your voice", "give me a reason to drink water",
        # hard negatives: tool-adjacent vocabulary in purely conversational turns
        "I love making lists, it keeps me calm",
        "my friend set a timer once and forgot about it for a week",
        "do you like music", "what's your favourite song",
        "we talked about the weather for an hour, so boring",
        "I should really keep a journal one day",
        "remembering names has never been my strong suit",
        "the lights at the concert were amazing",
    ],
}
