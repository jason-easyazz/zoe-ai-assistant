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
        # List management keywords
        list_keywords = {
            "ListAdd": ["add", "put", "insert", "include", "buy"],
            "ListRemove": ["remove", "delete", "take off", "cross off"],
            "ListShow": ["show", "display", "what's on", "read", "list"],
            "ListClear": ["clear", "empty", "reset"],
            "ListComplete": ["done", "complete", "finished", "check off"],
        }
        
        # Home Assistant keywords
        ha_keywords = {
            "HassTurnOn": ["turn on", "switch on", "enable"],
            "HassTurnOff": ["turn off", "switch off", "disable"],
            "HassToggle": ["toggle", "flip"],
        }
        
        # Time & weather keywords
        time_keywords = {
            "TimeNow": ["what time", "current time", "time is it"],
            "WeatherCurrent": ["weather", "temperature", "how hot", "how cold"],
        }
        
        # Add all keywords
        for intent, keywords in {**list_keywords, **ha_keywords, **time_keywords}.items():
            for keyword in keywords:
                self.keyword_processor.add_keyword(keyword, intent)
    
    def classify(self, text: str) -> Optional[ZoeIntent]:
        """
        Classify using keyword matching.
        
        Args:
            text: User input text
            
        Returns:
            ZoeIntent if keywords matched, None otherwise
        """
        start_time = time.time()
        
        # Extract keywords
        keywords_found = self.keyword_processor.extract_keywords(text, span_info=True)
        
        if keywords_found:
            # Use first matched keyword
            intent_name, start, end = keywords_found[0]
            
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
        
        logger.info("Initialized UnifiedIntentClassifier with Tier 0 (HassIL) and Tier 1 (Keywords)")
    
    def classify(self, text: str, min_confidence: float = 0.7) -> Optional[ZoeIntent]:
        """
        Classify text using multi-tier approach.
        
        Args:
            text: User input
            min_confidence: Minimum confidence threshold (default: 0.7)
            
        Returns:
            ZoeIntent if classification succeeded, None to fall back to LLM
        """
        if not text or not text.strip():
            return None
        
        text = text.strip()
        
        # Tier 0: HassIL pattern matching
        intent = self.hassil.classify(text)
        if intent and intent.confidence >= min_confidence:
            return intent
        
        # Tier 1: Keyword fallback
        intent = self.keywords.classify(text)
        if intent and intent.confidence >= min_confidence:
            return intent
        
        # Tier 2/3: Return None → caller handles LLM
        logger.debug(f"No intent match for: '{text}' (will use LLM)")
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

