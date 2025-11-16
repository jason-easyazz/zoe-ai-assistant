"""
Streaming TTS Adapter for NeuTTS Air
Wraps the TTS service to work with LiveKit's streaming audio pipeline
"""

import asyncio
import logging
from typing import Optional
import httpx
import io
from livekit.agents import tts
from livekit import rtc

logger = logging.getLogger(__name__)


class NeuTTSTTS(tts.TTS):
    """
    NeuTTS Air TTS adapter for LiveKit
    Streams audio from Zoe's TTS service
    """
    
    def __init__(
        self,
        service_url: str,
        voice_profile: str = "default",
        user_id: Optional[str] = None,
        speed: float = 1.0
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=24000,  # NeuTTS Air outputs 24kHz
            num_channels=1
        )
        self.service_url = service_url
        self.voice_profile = voice_profile
        self.user_id = user_id
        self.speed = speed
        self._client = httpx.AsyncClient(timeout=120.0)  # Longer timeout for Pi
    
    async def synthesize(self, text: str) -> "ChunkedStream":
        """
        Synthesize text to speech
        
        Returns a ChunkedStream that yields audio frames
        """
        logger.info(f"Synthesizing: '{text[:50]}...' with voice '{self.voice_profile}'")
        
        try:
            # Request synthesis from TTS service
            response = await self._client.post(
                f"{self.service_url}/synthesize",
                json={
                    "text": text,
                    "voice": self.voice_profile,
                    "speed": self.speed,
                    "use_cache": True,
                    "user_id": self.user_id
                }
            )
            response.raise_for_status()
            
            # Get audio data
            audio_data = response.content
            
            # Create stream
            return ChunkedStream(
                text=text,
                audio_data=audio_data,
                sample_rate=self.sample_rate,
                num_channels=self.num_channels
            )
        
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            # Return empty stream on error
            return ChunkedStream(
                text=text,
                audio_data=b"",
                sample_rate=self.sample_rate,
                num_channels=self.num_channels
            )
    
    async def aclose(self):
        """Close the HTTP client"""
        await self._client.aclose()


class ChunkedStream(tts.ChunkedStream):
    """
    Chunked audio stream for LiveKit
    Yields audio frames in chunks for smooth playback
    """
    
    def __init__(
        self,
        text: str,
        audio_data: bytes,
        sample_rate: int,
        num_channels: int,
        chunk_size: int = 4800  # 200ms at 24kHz
    ):
        super().__init__(text=text)
        self._audio_data = audio_data
        self._sample_rate = sample_rate
        self._num_channels = num_channels
        self._chunk_size = chunk_size
        self._position = 0
    
    async def _main_task(self):
        """
        Main task that yields audio chunks
        
        WAV files have a 44-byte header, so we skip it and stream the raw PCM data
        """
        if not self._audio_data:
            return
        
        # Skip WAV header (44 bytes)
        wav_header_size = 44
        pcm_data = self._audio_data[wav_header_size:]
        
        # Yield audio in chunks
        position = 0
        while position < len(pcm_data):
            chunk = pcm_data[position:position + self._chunk_size]
            
            if not chunk:
                break
            
            # Create audio frame
            frame = rtc.AudioFrame(
                data=chunk,
                sample_rate=self._sample_rate,
                num_channels=self._num_channels,
                samples_per_channel=len(chunk) // (2 * self._num_channels)  # 16-bit audio
            )
            
            self._event_ch.send_nowait(
                tts.SynthesizedAudio(
                    request_id=self._input.request_id,
                    frame=frame
                )
            )
            
            position += self._chunk_size
            
            # Small delay to simulate streaming (prevents buffer overflow)
            await asyncio.sleep(0.01)


# Streaming version removed for compatibility - using basic ChunkedStream only







