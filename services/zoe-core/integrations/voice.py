"""
Zoe v3.1 Voice Integration Service
Whisper STT + Coqui TTS Integration
"""

import aiohttp
import asyncio
import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class VoiceService:
    def __init__(self):
        self.whisper_url = os.getenv('WHISPER_URL', 'http://zoe-whisper:9001')
        self.tts_url = os.getenv('TTS_URL', 'http://zoe-tts:9002')
        self.enabled = os.getenv('VOICE_ENABLED', 'true').lower() == 'true'
        
    async def transcribe_audio(self, audio_data: bytes, content_type: str = 'audio/wav') -> Optional[str]:
        """Transcribe audio using Whisper STT service"""
        if not self.enabled:
            return None
            
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('audio_file', audio_data, content_type=content_type)
                
                async with session.post(f'{self.whisper_url}/asr', data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get('text', '').strip()
                    else:
                        logger.error(f"Whisper transcription failed: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Voice transcription error: {e}")
            return None
    
    async def synthesize_speech(self, text: str) -> Optional[bytes]:
        """Generate speech using TTS service"""
        if not self.enabled:
            return None
            
        try:
            async with aiohttp.ClientSession() as session:
                payload = {'text': text}
                
                async with session.post(f'{self.tts_url}/api/tts', json=payload) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.error(f"TTS synthesis failed: {response.status}")
                        return None
                        
        except Exception as e:
            logger.error(f"Speech synthesis error: {e}")
            return None
    
    async def health_check(self) -> Dict[str, Any]:
        """Check voice services health"""
        if not self.enabled:
            return {'whisper': 'disabled', 'tts': 'disabled'}
            
        health = {}
        
        try:
            async with aiohttp.ClientSession() as session:
                # Check Whisper
                async with session.get(f'{self.whisper_url}/docs') as response:
                    health['whisper'] = 'healthy' if response.status == 200 else 'unhealthy'
        except:
            health['whisper'] = 'offline'
            
        try:
            async with aiohttp.ClientSession() as session:
                # Check TTS
                async with session.get(f'{self.tts_url}/api/docs') as response:
                    health['tts'] = 'healthy' if response.status == 200 else 'unhealthy'
        except:
            health['tts'] = 'offline'
            
        return health

# Global instance
voice_service = VoiceService()
