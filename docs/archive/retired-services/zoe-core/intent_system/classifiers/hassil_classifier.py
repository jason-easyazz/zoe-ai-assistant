"""
HassIL-based Intent Classifier for Zoe
=======================================

Four-tier classification system:
- Tier 0: HassIL pattern matching (<5ms, 85-90% coverage)
- Tier 1: Keyword fallback (<15ms, 5-10% coverage)
- Tier 2: Context resolution (100-200ms, 3-5% coverage)
- Tier 3: LLM generative (<2% coverage, falls back to existing LLM)
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path
import yaml

try:
    from hassil import Intents, recognize
    from hassil.recognize import RecognizeResult
    from flashtext import KeywordProcessor
except ImportError:
    logging.error("HassIL dependencies not installed. Run: pip install hassil home-assistant-intents flashtext")
    raise

# Enhanced NLP imports for better entity extraction
try:
    import dateparser
    DATEPARSER_AVAILABLE = True
except ImportError:
    DATEPARSER_AVAILABLE = False
    logging.warning("dateparser not installed - date parsing will be limited")

try:
    from lingua_franca import load_language, parse as lf_parse
    from lingua_franca.parse import extract_duration, extract_number
    load_language("en-us")
    LINGUA_FRANCA_AVAILABLE = True
except ImportError:
    LINGUA_FRANCA_AVAILABLE = False
    logging.warning("lingua-franca not installed - spoken number parsing will be limited")

logger = logging.getLogger(__name__)


@dataclass
class ZoeIntent:
    """
    Structured intent result with confidence scoring.
    
    Attributes:
        name: Intent name (e.g., "ListAdd", "HassTurnOn")
        confidence: Confidence score 0.0-1.0
        slots: Extracted parameters (e.g., {"item": "bread", "list": "shopping"})
        tier: Classification tier used (0=HassIL, 1=Keyword, 2=Context, 3=LLM)
        original_text: User's original input
        latency_ms: Classification latency in milliseconds
    """
    name: str
    confidence: float
    slots: Dict[str, Any] = field(default_factory=dict)
    tier: int = 0
    original_text: str = ""
    latency_ms: float = 0.0


class HassilIntentClassifier:
    """
    Tier 0: HassIL pattern-based intent classification.
    
    Uses YAML patterns for deterministic, fast classification.
    Target: <5ms latency, 95-99% accuracy for matched patterns.
    """
    
    def __init__(self, intents_dir: str = "intent_system/intents/en"):
        """
        Initialize HassIL classifier.
        
        Args:
            intents_dir: Directory containing YAML intent files
        """
        self.intents_dir = Path(intents_dir)
        self.intents: Optional[Intents] = None
        self.user_lists: List[str] = []
        self.user_devices: List[str] = []
        self.user_areas: List[str] = []
        
        self._load_intents()
    
    def _load_intents(self):
        """Load all YAML intent files from directory."""
        if not self.intents_dir.exists():
            logger.warning(f"Intents directory not found: {self.intents_dir}")
            return
        
        # Collect all YAML files
        yaml_files = list(self.intents_dir.glob("*.yaml"))
        
        if not yaml_files:
            logger.warning(f"No YAML intent files found in {self.intents_dir}")
            return
        
        try:
            # Parse YAML files
            all_intent_data = []
            for yaml_file in yaml_files:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    if data:
                        all_intent_data.append(data)
                        logger.info(f"Loaded intent file: {yaml_file.name}")
            
            if all_intent_data:
                # Create HassIL Intents object
                self.intents = Intents.from_dict({"language": "en", "intents": {}})
                for data in all_intent_data:
                    if "intents" in data:
                        for intent_name, intent_def in data["intents"].items():
                            self.intents.intents[intent_name] = intent_def
                
                logger.info(f"Loaded {len(self.intents.intents)} intents from {len(yaml_files)} files")
            
        except Exception as e:
            logger.error(f"Failed to load intents: {e}", exc_info=True)
    
    def set_user_context(
        self,
        lists: Optional[List[str]] = None,
        devices: Optional[List[str]] = None,
        areas: Optional[List[str]] = None
    ):
        """
        Set user-specific context for dynamic slot filling.
        
        Args:
            lists: User's list names (e.g., ["shopping", "todo", "work"])
            devices: User's device names (e.g., ["living room light", "bedroom"])
            areas: User's area names (e.g., ["kitchen", "bedroom", "garage"])
        """
        if lists:
            self.user_lists = lists
        if devices:
            self.user_devices = devices
        if areas:
            self.user_areas = areas
    
    def classify(self, text: str) -> Optional[ZoeIntent]:
        """
        Classify text using HassIL pattern matching.
        
        Args:
            text: User input text
            
        Returns:
            ZoeIntent if matched, None otherwise
        """
        start_time = time.time()
        
        if not self.intents:
            return None
        
        try:
            # HassIL recognition
            result: Optional[RecognizeResult] = recognize(text, self.intents)
            
            if result:
                latency_ms = (time.time() - start_time) * 1000
                
                # Extract slots
                slots = {}
                if result.entities:
                    for entity_name, entity_value in result.entities.items():
                        slots[entity_name] = entity_value.get("value", entity_value)
                
                intent = ZoeIntent(
                    name=result.intent.name,
                    confidence=1.0,  # HassIL matches are deterministic
                    slots=slots,
                    tier=0,
                    original_text=text,
                    latency_ms=latency_ms
                )
                
                logger.info(
                    f"[Tier 0] HassIL match: {intent.name}, "
                    f"latency: {latency_ms:.2f}ms, slots: {slots}"
                )
                
                return intent
        
        except Exception as e:
            logger.debug(f"HassIL classification failed: {e}")
        
        return None


class KeywordFallbackClassifier:
    """
    Tier 1: Fast keyword-based fallback classifier.
    
    Uses FlashText for O(n) keyword matching when HassIL patterns don't match.
    Target: <15ms latency.
    """
    
    def __init__(self):
        """Initialize keyword processor."""
        self.keyword_processor = KeywordProcessor(case_sensitive=False)
        self._init_keywords()
    
    def _init_keywords(self):
        """Initialize keyword mappings for common intents."""
        # List management keywords - more specific patterns first
        # CRITICAL: Longer/more specific patterns MUST come first due to longest-match priority
        list_keywords = {
            "ListAdd": [
                # Very specific patterns (highest priority)
                "add to shopping list", "add to the shopping list", "add to my shopping list",
                "add to grocery list", "add to todo list", "add to work list", "add to my list",
                "put on shopping list", "put on the shopping list", "put on my shopping list",
                # Phrase patterns
                "add to shopping", "add to list", "add to todo", "add to work",
                "put on shopping", "put on list", "put on my list",
                "shopping list add", "todo list add", "grocery list add",
                # Task patterns  
                "todo", "personal task", "bucket list",
                # Purchase intent
                "buy", "need to buy", "i need to buy", "we need to buy",
                "we need", "we're out of", "out of",
                "remember to buy", "remember to get", "don't forget to get", "don't forget to buy",
                "get some", "pick up some", "grab some",
                # Generic add (lowest priority)
                "add", "put", "insert", "include",
            ],
            "ListRemove": ["remove from", "delete from", "take off", "cross off", "we got the", "got the", "remove", "delete"],
            "ListShow": [
                # Specific list shows (must NOT conflict with add patterns)
                "show shopping list", "show my shopping", "show my list", "show the list",
                "what's on my shopping", "what's on shopping", "what do i need to buy",
                "read my list", "read shopping", "read the list",
            ],
            "ListClear": ["clear my list", "clear shopping", "clear the list", "empty my list", "empty the list", "reset list"],
            "ListComplete": ["mark as done", "check off", "done with", "finished with", "complete", "mark", "done"],
        }
        
        # Calendar keywords - more specific patterns
        calendar_keywords = {
            "CalendarShow": [
                "show my calendar", "show calendar", "show my schedule", "show schedule",
                "what's on my calendar", "my calendar", "my events", "upcoming events",
                "what events", "my appointments", "my schedule",
            ],
            "CalendarToday": ["today's schedule", "today's events", "what do i have today", "events today", "schedule today"],
            "CalendarAdd": ["add event", "create event", "schedule a", "schedule meeting", "add appointment", "create appointment", "add to calendar"],
            "CalendarDelete": ["delete my meeting", "delete event", "cancel event", "remove event", "delete meeting", "cancel meeting", "delete appointment", "cancel appointment", "delete my"],
        }
        
        # Home Assistant keywords - comprehensive patterns
        ha_keywords = {
            "HassTurnOn": ["turn on", "switch on", "enable", "lights on", "light on", "turn the lights on", "turn lights on"],
            "HassTurnOff": ["turn off", "switch off", "disable", "lights off", "light off", "turn the lights off", "turn lights off", "shut off"],
            "HassToggle": ["toggle", "flip", "toggle the"],
            "HassSetBrightness": ["set brightness", "dim the", "dim to", "brighten the", "brightness to", "set the brightness", "dim", "brighten", "dim living room", "dim bedroom", "dim kitchen"],
            "HassSetColor": [
                "set color", "change color", "set the color", "change the color",
                "make it red", "make it blue", "make it green", "make it yellow", "make it purple", "make it pink", "make it white",
                "make the kitchen red", "make the bedroom blue", "make the living room",
                "make kitchen red", "make bedroom blue", "make living room",
                "kitchen red", "bedroom blue", "living room red",
                "to red", "to blue", "to green", "to yellow", "to purple", "to pink", "to white",
            ],
            "HassClimateSetTemperature": ["set thermostat", "thermostat to", "set the temperature to", "set temperature to", "make it degrees", "degrees inside"],
            "HassCoverOpen": ["open blinds", "open curtains", "raise blinds", "open the blinds", "raise the blinds", "open the curtains", "raise the curtains"],
            "HassCoverClose": ["close blinds", "close curtains", "lower blinds", "close the blinds", "lower the blinds", "close the curtains", "lower the curtains"],
            "HassLockDoor": ["lock door", "lock the door", "secure door", "lock the front", "lock front door", "lock the front door"],
            "HassUnlockDoor": ["unlock door", "unlock the door", "unlock the front", "unlock front door", "unlock the front door"],
        }
        
        # Time & weather keywords (order matters - more specific first)
        time_keywords = {
            "TimeNow": ["what time", "current time", "time is it", "what's the time", "tell me the time", "time please", "have the time", "got the time"],
            "DateToday": ["what day", "today's date", "what's the date", "what date", "whats the date", "the date", "what's today's date", "whats today", "what is today"],
            "TimerSet": ["set timer", "set a timer", "timer for", "start timer", "start a", "minute timer", "remind me in"],
            "TimerShow": ["show timer", "show timers", "my timers", "active timers", "how much time left", "time left", "timer status", "check timer"],
            "TimerCancel": ["cancel timer", "stop timer", "clear timer", "cancel the timer"],
            "WeatherCurrent": ["weather", "temperature", "how hot", "how cold", "is it cold", "is it hot", "outside"],
            "WeatherForecast": ["forecast", "weather forecast", "will it rain", "is it going to rain", "rain tomorrow", "weather tomorrow"],
        }
        
        # Greeting keywords (stricter matching applied in classify method)
        greeting_keywords = {
            "Greeting": ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "hi zoe", "hey zoe", "hello zoe"],
            "Goodbye": ["bye", "goodbye", "see you", "goodnight", "good night", "talk to you later", "bye zoe"],
            "Thanks": ["thanks", "thank you", "thanks zoe", "appreciate it"],
            "Help": ["help", "help me", "what can you do", "what are your commands", "list commands"],
        }
        
        # Music keywords
        music_keywords = {
            "MusicPlay": ["play", "play some", "put on", "play music", "i want to listen to", "i want to hear", "can you play"],
            "MusicPause": ["pause", "pause music", "stop music", "stop playing"],
            "MusicResume": ["resume", "resume music", "continue", "continue playing", "unpause", "keep playing"],
            "MusicSkip": ["skip", "skip this", "next song", "next track", "play next"],
            "MusicPrevious": ["previous", "previous song", "go back", "last song", "play previous"],
            "MusicVolume": ["volume", "louder", "quieter", "turn it up", "turn it down", "turn up the volume", "turn down the volume"],
            "MusicQueue": ["whats in the queue", "show queue", "whats playing next", "what song is next"],
            "MusicQueueAdd": ["add to queue", "queue up"],
            "MusicNowPlaying": ["what song is this", "whats playing", "what is playing", "who sings this", "who is this"],
            "MusicSearch": ["search for", "find me"],
            # Recommendation intents
            "MusicSimilar": ["play something similar", "more like this", "similar songs", "play similar", "something similar"],
            "MusicRadio": ["play my radio", "start my radio", "personal radio", "start radio", "shuffle my favorites", "play personalized music"],
            "MusicDiscover": ["discover new music", "find me something new", "play discovery mix", "show me new music", "surprise me", "play something new", "something i havent heard"],
            "MusicMood": ["play something for this mood", "match my mood", "more of this vibe", "keep this vibe going", "play something that fits"],
            "MusicLike": ["like this song", "i like this", "love this song", "add to favorites", "favorite this", "thumbs up"],
            "MusicStats": ["show my listening stats", "my music stats", "how much have i listened", "what are my top songs", "what are my favorite artists", "my listening history"],
        }
        
        # Add all keywords
        for intent, keywords in {**list_keywords, **calendar_keywords, **ha_keywords, **time_keywords, **greeting_keywords, **music_keywords}.items():
            for keyword in keywords:
                self.keyword_processor.add_keyword(keyword, intent)
    
    def classify(self, text: str) -> Optional[ZoeIntent]:
        """
        Classify using keyword matching with priority ordering.
        
        Args:
            text: User input text
            
        Returns:
            ZoeIntent if keywords matched, None otherwise
        """
        start_time = time.time()
        
        # Extract keywords
        keywords_found = self.keyword_processor.extract_keywords(text, span_info=True)
        
        if keywords_found:
            # Use LONGEST matched keyword (most specific) instead of first
            # This resolves conflicts like "shopping list" vs "list"
            keywords_found.sort(key=lambda x: x[2] - x[1], reverse=True)
            intent_name, start, end = keywords_found[0]
            
            # Filter out false positives for short/ambiguous matches
            text_lower = text.lower().strip()
            
            # For greeting/thanks intents, apply stricter matching
            if intent_name == "Thanks" and text_lower not in ["thanks", "thank you", "thanks zoe", "appreciate it"]:
                # Check if "thanks" appears with negation or other action words
                if any(word in text_lower for word in ["no thanks", "remove", "delete", "cancel"]):
                    return None  # Let it fall through to other handlers
            
            if intent_name == "Greeting" and text_lower not in ["hi", "hello", "hey", "hi zoe", "hey zoe", "hello zoe", 
                                                                  "good morning", "good afternoon", "good evening"]:
                return None  # False positive - let LLM handle
            
            if intent_name == "Goodbye" and text_lower not in ["bye", "goodbye", "bye zoe", "goodnight", "good night", 
                                                                 "see you", "see you later", "talk to you later"]:
                return None  # False positive
            
            latency_ms = (time.time() - start_time) * 1000
            
            # Simple slot extraction (basic pattern matching)
            slots = self._extract_simple_slots(text, intent_name)
            
            intent = ZoeIntent(
                name=intent_name,
                confidence=0.75,  # Lower confidence for keyword matching
                slots=slots,
                tier=1,
                original_text=text,
                latency_ms=latency_ms
            )
            
            logger.info(
                f"[Tier 1] Keyword match: {intent.name}, "
                f"latency: {latency_ms:.2f}ms, confidence: {intent.confidence}"
            )
            
            return intent
        
        return None
    
    def _extract_simple_slots(self, text: str, intent_name: str) -> Dict[str, Any]:
        """
        Extract simple slots using basic pattern matching.
        
        Args:
            text: User input
            intent_name: Matched intent
            
        Returns:
            Dictionary of extracted slots
        """
        slots = {}
        text_lower = text.lower()
        
        # List type detection (order matters - most specific first!)
        if "bucket list" in text_lower or "bucketlist" in text_lower:
            slots["list"] = "bucket"
        elif "work list" in text_lower or "work todo" in text_lower:
            slots["list"] = "work_todos"
        elif "personal list" in text_lower or "personal todo" in text_lower or "my todo" in text_lower:
            slots["list"] = "personal_todos"
        elif "shopping" in text_lower or "grocery" in text_lower or "groceries" in text_lower:
            slots["list"] = "shopping"
        elif "todo" in text_lower or "to do" in text_lower or "to-do" in text_lower:
            slots["list"] = "personal_todos"
        elif "work" in text_lower:
            slots["list"] = "work_todos"
        
        # For ListAdd, try to extract item
        if intent_name == "ListAdd":
            # Pattern: "add X to Y list" or "add X"
            import re
            
            # Try: "add X to (the) [list type] list"
            match = re.search(r'(?:add|put|buy)\s+(.+?)\s+to\s+(?:the\s+)?(?:my\s+)?(?:bucket|work|personal|shopping|todo|to\s*do)\s*list', text_lower)
            if match:
                slots["item"] = match.group(1).strip()
            else:
                # Try: "add X to [list type]"
                match = re.search(r'(?:add|put|buy)\s+(.+?)\s+to\s+(?:bucket|work|personal|shopping|todo)', text_lower)
                if match:
                    slots["item"] = match.group(1).strip()
                else:
                    # Fallback: find text after "add"/"buy"/"put"
                    for trigger in ["add ", "buy ", "put "]:
                        if trigger in text_lower:
                            after_trigger = text[text_lower.index(trigger) + len(trigger):].strip()
                            # Extract until "to" or end
                            item = after_trigger.split(" to ")[0].strip()
                            if item:
                                slots["item"] = item
                            break
        
        # Timer duration extraction - use lingua-franca for spoken numbers
        if intent_name in ["TimerSet", "TimerCancel"]:
            import re
            
            # Try lingua-franca first for spoken numbers like "five minutes"
            if LINGUA_FRANCA_AVAILABLE:
                try:
                    duration_result = extract_duration(text)
                    if duration_result:
                        duration_td, remaining = duration_result
                        total_seconds = duration_td.total_seconds()
                        if total_seconds >= 3600:
                            slots["duration"] = f"{int(total_seconds // 3600)} hours"
                        elif total_seconds >= 60:
                            slots["duration"] = f"{int(total_seconds // 60)} minutes"
                        else:
                            slots["duration"] = f"{int(total_seconds)} seconds"
                        slots["duration_seconds"] = int(total_seconds)
                except Exception as e:
                    logger.debug(f"lingua-franca duration extraction failed: {e}")
            
            # Fallback to regex patterns
            if "duration" not in slots:
                duration_patterns = [
                    r'for\s+(\d+)\s*(?:minute|min|hour|hr|second|sec)s?',
                    r'(\d+)\s*(?:minute|min|hour|hr|second|sec)s?\s+timer',
                    r'in\s+(\d+)\s*(?:minute|min|hour|hr|second|sec)s?',
                ]
                for pattern in duration_patterns:
                    match = re.search(pattern, text_lower)
                    if match:
                        num = int(match.group(1))
                        if "hour" in text_lower or "hr" in text_lower:
                            slots["duration"] = f"{num} hours"
                            slots["duration_seconds"] = num * 3600
                        elif "second" in text_lower or "sec" in text_lower:
                            slots["duration"] = f"{num} seconds"
                            slots["duration_seconds"] = num
                        else:
                            slots["duration"] = f"{num} minutes"
                            slots["duration_seconds"] = num * 60
                        break
        
        # Home Assistant device name extraction
        if intent_name in ["HassTurnOn", "HassTurnOff", "HassToggle"]:
            import re
            # Pattern: "turn on/off [the] [device name]" or "toggle [the] [device]"
            name_patterns = [
                r'(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(.+?)(?:\s+light)?$',
                r'(?:the\s+)?(.+?)\s+(?:on|off)$',
                r'(?:enable|disable)\s+(?:the\s+)?(.+?)$',
                r'toggle\s+(?:the\s+)?(.+?)(?:\s+light)?$',
                r'flip\s+(?:the\s+)?(.+?)(?:\s+light)?$',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    name = match.group(1).strip()
                    # Remove trailing "light/lights" if present
                    name = re.sub(r'\s+lights?$', '', name)
                    if name and name not in ["the", "a", "my"]:
                        slots["name"] = name
                    break
        
        # Brightness extraction
        if intent_name == "HassSetBrightness":
            import re
            # Pattern: "set X to Y percent" or "dim X to Y"
            brightness_match = re.search(r'(\d+)\s*(?:percent|%)?', text_lower)
            if brightness_match:
                slots["brightness"] = brightness_match.group(1)
            # Extract device name
            name_match = re.search(r'(?:set|dim|brighten)\s+(?:the\s+)?(.+?)\s+(?:to|brightness)', text_lower)
            if name_match:
                slots["name"] = name_match.group(1).strip()
        
        # Color extraction
        if intent_name == "HassSetColor":
            import re
            colors = ["red", "green", "blue", "yellow", "orange", "purple", "pink", "white", "cyan"]
            for color in colors:
                if color in text_lower:
                    slots["color"] = color
                    break
            # Extract device name
            name_match = re.search(r'(?:set|make|change|turn)\s+(?:the\s+)?(.+?)\s+(?:to|color)', text_lower)
            if name_match:
                slots["name"] = name_match.group(1).strip()
        
        # Temperature extraction
        if intent_name == "HassClimateSetTemperature":
            import re
            temp_match = re.search(r'(\d+)\s*(?:degrees?|°)?', text_lower)
            if temp_match:
                slots["temperature"] = temp_match.group(1)
        
        # Cover name extraction
        if intent_name in ["HassCoverOpen", "HassCoverClose"]:
            import re
            if "blinds" in text_lower:
                slots["name"] = "blinds"
            elif "curtains" in text_lower:
                slots["name"] = "curtains"
            elif "shades" in text_lower:
                slots["name"] = "shades"
            # Try to extract room name
            room_match = re.search(r'(?:the\s+)?(\w+)\s+(?:blinds|curtains|shades)', text_lower)
            if room_match:
                slots["name"] = f"{room_match.group(1)} {slots.get('name', 'blinds')}"
        
        # Calendar event extraction with dateparser
        if intent_name in ["CalendarAdd", "CalendarDelete", "CalendarShow", "CalendarToday"]:
            import re
            
            # Extract event title
            title_patterns = [
                r'(?:schedule|add|create)\s+(?:a\s+)?(?:event\s+)?(?:called\s+)?(.+?)(?:\s+(?:at|on|for|tomorrow|today|next))',
                r'(?:schedule|add|create)\s+(?:a\s+)?(.+?)(?:\s+meeting|\s+event|\s+appointment)',
                r'(?:schedule|add|create)\s+(?:a\s+)?(?:meeting|event|appointment)\s+(?:called\s+)?(.+?)(?:\s+(?:at|on|for))',
                r'(?:schedule|add|create)\s+(.+?)$',
            ]
            for pattern in title_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    title = match.group(1).strip()
                    # Clean up common words
                    title = re.sub(r'^(?:a\s+|an\s+|the\s+)', '', title)
                    if title and title not in ["event", "meeting", "appointment"]:
                        slots["title"] = title
                    break
            
            # Extract date/time using dateparser
            if DATEPARSER_AVAILABLE:
                try:
                    # Look for time patterns like "at 3pm", "at noon"
                    time_match = re.search(r'at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|noon|midnight)', text_lower)
                    if time_match:
                        parsed = dateparser.parse(time_match.group(1))
                        if parsed:
                            slots["time"] = parsed.strftime("%H:%M")
                    
                    # Look for date patterns like "tomorrow", "next Tuesday"
                    date_patterns = ["tomorrow", "today", "next week", "next monday", "next tuesday", 
                                     "next wednesday", "next thursday", "next friday", "next saturday", "next sunday"]
                    for dp in date_patterns:
                        if dp in text_lower:
                            parsed = dateparser.parse(dp)
                            if parsed:
                                slots["date"] = parsed.strftime("%Y-%m-%d")
                            break
                except Exception as e:
                    logger.debug(f"dateparser failed: {e}")
        
        # Music slot extraction
        if intent_name in ["MusicPlay", "MusicSearch", "MusicQueueAdd"]:
            import re
            
            # Extract query - everything after play/search/queue trigger words
            music_patterns = [
                r'(?:play|put\s+on)\s+(?:some\s+)?(.+?)$',
                r'(?:search\s+for|find(?:\s+me)?)\s+(.+?)$',
                r'(?:add|queue(?:\s+up)?)\s+(.+?)(?:\s+to\s+(?:the\s+)?queue)?$',
                r'i\s+want\s+to\s+(?:listen\s+to|hear)\s+(.+?)$',
                r'can\s+you\s+play\s+(.+?)$',
            ]
            
            for pattern in music_patterns:
                match = re.search(pattern, text_lower)
                if match:
                    query = match.group(1).strip()
                    # Clean up common trailing words
                    query = re.sub(r'\s+(?:music|please|for\s+me)$', '', query)
                    if query and query not in ["music", "some", "something"]:
                        slots["query"] = query
                    break
        
        # Music volume extraction
        if intent_name == "MusicVolume":
            import re
            # Numeric volume
            vol_match = re.search(r'(\d+)\s*(?:percent|%)?', text_lower)
            if vol_match:
                slots["level"] = vol_match.group(1)
            # Relative volume (handled in handler)
            elif any(word in text_lower for word in ["up", "louder", "raise", "increase"]):
                slots["level"] = "up"
            elif any(word in text_lower for word in ["down", "quieter", "lower", "softer", "decrease"]):
                slots["level"] = "down"
        
        return slots


class UnifiedIntentClassifier:
    """
    Unified multi-tier intent classifier.
    
    Orchestrates all classification tiers:
    1. Try HassIL (Tier 0) - fastest, most reliable
    2. Try Keywords (Tier 1) - fast fallback
    3. Return None → LLM (Tier 2/3) - handled by caller
    """
    
    def __init__(self, intents_dir: str = "intent_system/intents/en"):
        """
        Initialize unified classifier.
        
        Args:
            intents_dir: Directory containing YAML intent files
        """
        self.hassil = HassilIntentClassifier(intents_dir)
        self.keywords = KeywordFallbackClassifier()

        # Phase -1 Fix 3: Tier hit rate counters for measuring deterministic coverage
        self._tier_counts = {"tier_0": 0, "tier_1": 0, "tier_context": 0, "tier_llm": 0, "total": 0}
        
        logger.info("Initialized UnifiedIntentClassifier with Tier 0 (HassIL) and Tier 1 (Keywords)")
    
    def classify(
        self, 
        text: str, 
        min_confidence: float = 0.7,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Optional[ZoeIntent]:
        """
        Classify text using multi-tier approach.
        
        Args:
            text: User input
            min_confidence: Minimum confidence threshold (default: 0.7)
            user_id: User ID for context lookup
            session_id: Session ID for context lookup
            
        Returns:
            ZoeIntent if classification succeeded, None to fall back to LLM
        """
        if not text or not text.strip():
            return None
        
        text = text.strip()
        self._tier_counts["total"] += 1
        
        # Tier 0: HassIL pattern matching
        intent = self.hassil.classify(text)
        if intent and intent.confidence >= min_confidence:
            self._tier_counts["tier_0"] += 1
            self._log_tier_rates()
            return intent
        
        # Tier 1: Keyword fallback
        intent = self.keywords.classify(text)
        if intent and intent.confidence >= min_confidence:
            self._tier_counts["tier_1"] += 1
            self._log_tier_rates()
            return intent
        
        # Tier 1.5: Context-aware continuation (e.g., "and eggs" → ListAdd)
        if user_id and session_id:
            intent = self._classify_with_context(text, user_id, session_id)
            if intent and intent.confidence >= min_confidence:
                self._tier_counts["tier_context"] += 1
                self._log_tier_rates()
                return intent
        
        # Tier 2/3: Return None → caller handles LLM
        self._tier_counts["tier_llm"] += 1
        self._log_tier_rates()
        logger.debug(f"No intent match for: '{text}' (will use LLM)")
        return None
    
    def _log_tier_rates(self):
        """Log tier hit rates every 50 classifications for monitoring."""
        total = self._tier_counts["total"]
        if total > 0 and total % 50 == 0:
            t0 = self._tier_counts["tier_0"]
            t1 = self._tier_counts["tier_1"]
            tc = self._tier_counts["tier_context"]
            tl = self._tier_counts["tier_llm"]
            logger.info(
                f"📊 Intent tier hit rates ({total} total): "
                f"Tier 0 (HassIL): {t0} ({100*t0/total:.1f}%), "
                f"Tier 1 (Keywords): {t1} ({100*t1/total:.1f}%), "
                f"Tier 1.5 (Context): {tc} ({100*tc/total:.1f}%), "
                f"LLM fallback: {tl} ({100*tl/total:.1f}%)"
            )

    def get_tier_stats(self) -> dict:
        """Return current tier hit rate statistics.
        
        Phase -1 Fix 3: Measurement endpoint for tier rates so we can verify
        the 85-90% deterministic coverage claim before/after module intent fix.
        """
        total = self._tier_counts["total"]
        if total == 0:
            return {"total": 0, "message": "No classifications yet"}
        return {
            "total": total,
            "tier_0_hassil": {"count": self._tier_counts["tier_0"], "pct": round(100 * self._tier_counts["tier_0"] / total, 1)},
            "tier_1_keywords": {"count": self._tier_counts["tier_1"], "pct": round(100 * self._tier_counts["tier_1"] / total, 1)},
            "tier_context": {"count": self._tier_counts["tier_context"], "pct": round(100 * self._tier_counts["tier_context"] / total, 1)},
            "tier_llm_fallback": {"count": self._tier_counts["tier_llm"], "pct": round(100 * self._tier_counts["tier_llm"] / total, 1)},
            "deterministic_pct": round(100 * (self._tier_counts["tier_0"] + self._tier_counts["tier_1"] + self._tier_counts["tier_context"]) / total, 1),
        }

    def _classify_with_context(
        self,
        text: str,
        user_id: str,
        session_id: str
    ) -> Optional[ZoeIntent]:
        """
        Classify using conversation context for continuation patterns.
        
        Handles patterns like:
        - "and eggs" after "add milk to shopping list" → ListAdd
        - "remove it" after list discussion → ListRemove
        """
        import re
        from intent_system.classifiers import get_context_manager
        
        context_manager = get_context_manager()
        context = context_manager.get_context(user_id, session_id)
        
        logger.debug(f"[Context] Checking context for user={user_id}, session={session_id}")
        logger.debug(f"[Context] last_intent={context.last_intent}, last_list={context.last_list}, expired={context.is_expired()}")
        
        # Check if context is valid and not expired
        if context.is_expired():
            logger.debug(f"[Context] Context expired, skipping context classification")
            return None
        
        text_lower = text.lower().strip()
        
        # Pattern: "and X" continuation
        # Example: "and eggs", "and also bread", "plus milk"
        continuation_match = re.match(
            r'^(?:and\s+(?:also\s+)?|plus\s+|also\s+)(.+)$',
            text_lower
        )
        
        if continuation_match and context.last_intent == "ListAdd" and context.last_list:
            item = continuation_match.group(1).strip()
            logger.info(f"[Context] Continuation detected: '{item}' → ListAdd to {context.last_list}")
            return ZoeIntent(
                name="ListAdd",
                confidence=0.75,
                slots={"item": item, "list": context.last_list},
                tier=1,
                original_text=text,
                latency_ms=0.0
            )
        
        # Pattern: "it"/"that"/"them" references
        if context.last_list and text_lower in ["show it", "show them", "show that"]:
            return ZoeIntent(
                name="ListShow",
                confidence=0.75,
                slots={"list": context.last_list},
                tier=1,
                original_text=text,
                latency_ms=0.0
            )
        
        return None
    
    def set_user_context(
        self,
        lists: Optional[List[str]] = None,
        devices: Optional[List[str]] = None,
        areas: Optional[List[str]] = None
    ):
        """Update user-specific context for dynamic slot filling."""
        self.hassil.set_user_context(lists, devices, areas)
    
    def get_available_intents(self) -> List[str]:
        """Get list of all available intent names."""
        if self.hassil.intents:
            return list(self.hassil.intents.intents.keys())
        return []

