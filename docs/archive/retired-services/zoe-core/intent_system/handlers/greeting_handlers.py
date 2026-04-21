"""
Greeting Intent Handlers
========================

Handles greeting and conversational intents:
- Greeting: Personalized hello
- Goodbye: Friendly sign-off
- Thanks: Acknowledgment
- Help: List available commands
"""

import logging
import sqlite3
import os
import random
from datetime import datetime
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)

# Database path
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Default timezone
DEFAULT_TIMEZONE = "Australia/Sydney"


def get_user_name(user_id: str) -> str:
    """Get user's first name from database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Try users table first
        cursor.execute("""
            SELECT first_name FROM users WHERE id = ?
        """, (user_id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            conn.close()
            return row[0]
        
        # Try user_profiles table
        cursor.execute("""
            SELECT display_name FROM user_profiles WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            return row[0].split()[0]  # Get first name from display name
            
    except Exception as e:
        logger.warning(f"Could not get user name: {e}")
    
    return ""


def get_time_of_day(user_id: str) -> str:
    """Get time of day greeting based on user's timezone."""
    try:
        # Try to get user timezone
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT setting_value FROM user_settings 
            WHERE user_id = ? AND setting_key = 'timezone'
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        tz_name = row[0] if row and row[0] else DEFAULT_TIMEZONE
        tz = ZoneInfo(tz_name)
        hour = datetime.now(tz).hour
        
    except Exception:
        hour = datetime.now().hour
    
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


async def handle_greeting(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle Greeting intent - personalized hello.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        user_name = get_user_name(user_id)
        time_of_day = get_time_of_day(user_id)
        
        # Varied greeting responses
        if user_name:
            greetings = [
                f"Good {time_of_day}, {user_name}! How can I help you?",
                f"Hey {user_name}! What can I do for you?",
                f"Hi {user_name}! Good {time_of_day}!",
                f"Hello {user_name}! How's it going?",
            ]
        else:
            greetings = [
                f"Good {time_of_day}! How can I help you?",
                "Hey there! What can I do for you?",
                "Hi! How can I help?",
                "Hello! What's on your mind?",
            ]
        
        greeting = random.choice(greetings)
        
        return {
            "success": True,
            "message": greeting,
            "data": {
                "user_name": user_name,
                "time_of_day": time_of_day
            }
        }
        
    except Exception as e:
        logger.error(f"Greeting handler failed: {e}", exc_info=True)
        return {
            "success": True,
            "message": "Hello! How can I help you?"
        }


async def handle_goodbye(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle Goodbye intent - friendly sign-off.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        user_name = get_user_name(user_id)
        time_of_day = get_time_of_day(user_id)
        
        # Time-appropriate goodbyes
        if time_of_day == "night":
            if user_name:
                goodbyes = [
                    f"Goodnight, {user_name}! Sleep well! 🌙",
                    f"Sweet dreams, {user_name}!",
                    f"Night, {user_name}! Talk to you tomorrow!",
                ]
            else:
                goodbyes = [
                    "Goodnight! Sleep well! 🌙",
                    "Sweet dreams!",
                    "Night! Talk to you later!",
                ]
        else:
            if user_name:
                goodbyes = [
                    f"Bye, {user_name}! Have a great {time_of_day}!",
                    f"See you later, {user_name}!",
                    f"Take care, {user_name}!",
                    f"Catch you later, {user_name}! 👋",
                ]
            else:
                goodbyes = [
                    f"Bye! Have a great {time_of_day}!",
                    "See you later!",
                    "Take care!",
                    "Catch you later! 👋",
                ]
        
        goodbye = random.choice(goodbyes)
        
        return {
            "success": True,
            "message": goodbye,
            "data": {
                "user_name": user_name,
                "time_of_day": time_of_day
            }
        }
        
    except Exception as e:
        logger.error(f"Goodbye handler failed: {e}", exc_info=True)
        return {
            "success": True,
            "message": "Goodbye! Take care!"
        }


async def handle_thanks(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle Thanks intent - acknowledgment.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        user_name = get_user_name(user_id)
        
        # Varied acknowledgments
        if user_name:
            responses = [
                f"You're welcome, {user_name}!",
                f"Happy to help, {user_name}!",
                f"Anytime, {user_name}! 😊",
                f"No problem, {user_name}!",
                f"My pleasure, {user_name}!",
            ]
        else:
            responses = [
                "You're welcome!",
                "Happy to help!",
                "Anytime! 😊",
                "No problem!",
                "My pleasure!",
            ]
        
        response = random.choice(responses)
        
        return {
            "success": True,
            "message": response,
            "data": {}
        }
        
    except Exception as e:
        logger.error(f"Thanks handler failed: {e}", exc_info=True)
        return {
            "success": True,
            "message": "You're welcome!"
        }


def get_available_capabilities() -> List[Dict[str, str]]:
    """Get list of available capabilities for help response."""
    return [
        {"category": "Lists", "examples": ["Add milk to shopping list", "Show my shopping list", "Remove bread from list"]},
        {"category": "Calendar", "examples": ["What's on my calendar today", "Schedule a meeting tomorrow at 2pm", "Show my events"]},
        {"category": "Weather", "examples": ["What's the weather", "Weather forecast", "Will it rain tomorrow"]},
        {"category": "Time", "examples": ["What time is it", "What's today's date", "Set a timer for 5 minutes"]},
        {"category": "Reminders", "examples": ["Remind me to call mom", "Set a reminder for tomorrow"]},
        {"category": "Memory", "examples": ["Remember that I like pizza", "What's my favorite food"]},
    ]


async def handle_help(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle Help intent - list available commands.
    
    Args:
        intent: Parsed intent with slots
        user_id: User identifier
        context: Conversation context
        
    Returns:
        Dict with success, message, and data
    """
    try:
        capabilities = get_available_capabilities()
        
        # Build help message
        message_lines = ["Here's what I can help you with:\n"]
        
        for cap in capabilities:
            message_lines.append(f"**{cap['category']}**")
            for example in cap['examples'][:2]:  # Show 2 examples per category
                message_lines.append(f"  • \"{example}\"")
            message_lines.append("")
        
        message_lines.append("Just ask naturally and I'll do my best to help! 😊")
        
        return {
            "success": True,
            "message": "\n".join(message_lines),
            "data": {
                "capabilities": capabilities
            }
        }
        
    except Exception as e:
        logger.error(f"Help handler failed: {e}", exc_info=True)
        return {
            "success": True,
            "message": "I can help with lists, calendar, weather, reminders, and more! Just ask naturally."
        }

