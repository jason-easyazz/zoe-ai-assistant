"""
Response Formatter
==================

Formats intent execution results as natural language responses.

Provides:
- Template-based response generation
- Multiple response variations (for naturalness)
- Slot interpolation
- Error message formatting
"""

import logging
import random
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# Response templates for each intent
RESPONSE_TEMPLATES = {
    # List intents
    "ListAdd": [
        "✅ Added {item} to your {list} list!",
        "Done! {item} is on the {list} list.",
        "Got it, added {item}.",
        "✓ {item} added to {list}.",
    ],
    "ListRemove": [
        "✅ Removed {item} from your {list} list!",
        "Done! Took {item} off the list.",
        "✓ {item} removed.",
    ],
    "ListShow": [
        "Here's your {list} list:\n{items}",
        "Your {list} list:\n{items}",
    ],
    "ListClear": [
        "✅ Cleared your {list} list!",
        "Done! Your {list} list is now empty.",
        "✓ All items removed from {list}.",
    ],
    "ListComplete": [
        "✅ Marked {item} as complete!",
        "Done! {item} is complete.",
        "✓ {item} checked off.",
    ],
    
    # Home Assistant intents
    "HassTurnOn": [
        "✅ Turned on {device}!",
        "{device} is now on.",
        "Done, {device} is on.",
    ],
    "HassTurnOff": [
        "✅ Turned off {device}!",
        "{device} is now off.",
        "Done, {device} is off.",
    ],
    "HassToggle": [
        "✅ Toggled {device}!",
        "Done! {device} toggled.",
    ],
    "HassSetBrightness": [
        "✅ Set {device} to {brightness}%!",
        "Done! {device} brightness is now {brightness}%.",
    ],
    "HassSetColor": [
        "✅ Changed {device} to {color}!",
        "Done! {device} is now {color}.",
    ],
    
    # Time & weather intents
    "TimeNow": [
        "It's {time}.",
        "The time is {time}.",
        "Currently {time}.",
    ],
    "WeatherCurrent": [
        "It's {temperature}° and {condition} right now.",
        "Currently {temperature}° with {condition}.",
    ],
    
    # Calendar intents
    "CalendarCreate": [
        "✅ Created event: {event}!",
        "Done! Added {event} to your calendar.",
        "✓ Event created: {event}.",
    ],
    "CalendarShow": [
        "Here are your upcoming events:\n{events}",
        "Your calendar:\n{events}",
    ],
    
    # Error templates
    "error_no_handler": [
        "I don't know how to do that yet.",
        "Sorry, I'm not sure how to handle that.",
        "I can't help with that right now.",
    ],
    "error_execution": [
        "Sorry, I encountered an error while processing that.",
        "I had trouble doing that. Can you try again?",
        "Something went wrong. Please try again.",
    ],
    "error_missing_param": [
        "I didn't catch all the details. Can you repeat that?",
        "Sorry, what did you want me to do?",
        "Can you provide more information?",
    ],
}


class ResponseFormatter:
    """
    Formats intent execution results as natural language.
    """
    
    def __init__(self, templates: Optional[Dict[str, List[str]]] = None):
        """
        Initialize response formatter.
        
        Args:
            templates: Custom response templates (optional)
        """
        self.templates = templates or RESPONSE_TEMPLATES
        logger.info("Initialized ResponseFormatter")
    
    def format_response(
        self,
        intent_name: str,
        slots: Dict[str, Any],
        success: bool = True,
        custom_message: Optional[str] = None
    ) -> str:
        """
        Format a response for an intent execution.
        
        Args:
            intent_name: Name of the intent
            slots: Intent slots (parameters)
            success: Whether execution succeeded
            custom_message: Override template with custom message
            
        Returns:
            Formatted natural language response
        """
        # Use custom message if provided
        if custom_message:
            return custom_message
        
        # Get templates for this intent
        templates = self.templates.get(intent_name)
        
        if not templates:
            logger.warning(f"No template found for intent: {intent_name}")
            return self._format_default(intent_name, slots, success)
        
        # Choose random template for variation
        template = random.choice(templates)
        
        # Interpolate slots into template
        try:
            response = template.format(**slots)
            return response
        except KeyError as e:
            logger.warning(f"Missing slot in template: {e}")
            return self._format_default(intent_name, slots, success)
    
    def _format_default(
        self,
        intent_name: str,
        slots: Dict[str, Any],
        success: bool
    ) -> str:
        """
        Format a default response when no template exists.
        
        Args:
            intent_name: Name of the intent
            slots: Intent slots
            success: Whether execution succeeded
            
        Returns:
            Generic formatted response
        """
        if success:
            return f"✅ {intent_name} completed successfully."
        else:
            return f"❌ {intent_name} failed."
    
    def format_error(
        self,
        error_type: str = "execution",
        details: Optional[str] = None
    ) -> str:
        """
        Format an error message.
        
        Args:
            error_type: Type of error (no_handler, execution, missing_param)
            details: Additional error details
            
        Returns:
            Formatted error message
        """
        template_key = f"error_{error_type}"
        templates = self.templates.get(template_key, ["An error occurred."])
        
        message = random.choice(templates)
        
        if details:
            message += f" ({details})"
        
        return message
    
    def add_template(self, intent_name: str, templates: List[str]):
        """
        Add or update templates for an intent.
        
        Args:
            intent_name: Name of the intent
            templates: List of template strings
        """
        self.templates[intent_name] = templates
        logger.debug(f"Added {len(templates)} templates for {intent_name}")


# Global singleton
_formatter: Optional[ResponseFormatter] = None


def get_response_formatter() -> ResponseFormatter:
    """Get global ResponseFormatter singleton."""
    global _formatter
    if _formatter is None:
        _formatter = ResponseFormatter()
    return _formatter

