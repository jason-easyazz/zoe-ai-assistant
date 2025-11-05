"""
Local Whisper STT Adapter for LiveKit Voice Assistant
Uses Zoe's existing zoe-whisper service instead of OpenAI
"""

import asyncio
import logging
import httpx
import io
from typing import Optional
from livekit.agents import stt
from livekit import rtc
import wave

logger = logging.getLogger(__name__)


class LocalWhisperSTT(stt.STT):
    """
    Speech-to-Text adapter using Zoe's local Whisper service
    Completely free and private!
    """
    
    def __init__(
        self,
        whisper_url: str = "http://localhost:9001",
        language: str = "en"
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(streaming=False, interim_results=False)
        )
        self.whisper_url = whisper_url
        self.language = language
        
        logger.info(f"Local Whisper STT initialized: {whisper_url}")
    
    async def recognize(
        self,
        *,
        buffer: rtc.AudioFrame,
        language: Optional[str] = None,
    ) -> stt.SpeechEvent:
        """
        Recognize speech from audio buffer
        
        Args:
            buffer: Audio data to transcribe
            language: Language code (default: en)
        
        Returns:
            SpeechEvent with transcribed text
        """
        # Convert audio frame to WAV format
        # LiveKit audio is float32, convert to int16
        import numpy as np
        
        audio_np = np.frombuffer(buffer.data, dtype=np.int16)
        sample_rate = buffer.sample_rate
        num_channels = buffer.num_channels
        
        logger.info(f"Audio: {len(audio_np)} samples, {sample_rate}Hz, {num_channels} ch")
        
        # Create WAV file in memory
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(num_channels)
            wav_file.setsampwidth(2)  # 16-bit audio
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_np.tobytes())
        
        wav_io.seek(0)
        
        try:
            # Send to local Whisper service (longer timeout for Pi 5)
            async with httpx.AsyncClient(timeout=90.0) as client:
                files = {
                    'file': ('audio.wav', wav_io, 'audio/wav')  # Changed 'audio' to 'file'
                }
                
                response = await client.post(
                    f"{self.whisper_url}/transcribe",
                    files=files
                )
                response.raise_for_status()
                result = response.json()
                
                # Extract transcription
                text = result.get('text', '').strip()
                
                if not text:
                    logger.warning("Empty transcription from Whisper")
                    return stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[
                            stt.SpeechData(
                                language=language or self.language,
                                text=""
                            )
                        ]
                    )
                
                logger.info(f"Transcribed: '{text}'")
                
                return stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    alternatives=[
                        stt.SpeechData(
                            language=language or self.language,
                            text=text,
                            confidence=1.0
                        )
                    ]
                )
        
        except httpx.TimeoutException:
            logger.error("Whisper transcription timeout")
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(
                        language=language or self.language,
                        text=""
                    )
                ]
            )
        
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(
                        language=language or self.language,
                        text=""
                    )
                ]
            )

