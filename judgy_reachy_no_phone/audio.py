"""Local response selection and local Kokoro text-to-speech."""

import asyncio
import logging
import os
import tempfile
import threading

from .config import PERSONALITIES, get_random_personality

logger = logging.getLogger(__name__)


class LLMResponder:
    """Use only the checked-in personality lines; no network LLM is used."""

    def __init__(self, api_key: str = "", personality: str = "mixtape"):
        self.personality = personality
        self.client = None  # Kept for UI compatibility.
        if api_key:
            logger.warning("Ignoring Groq key: this build is local-only.")

    def _personality(self):
        key = get_random_personality() if self.personality == "mixtape" else self.personality
        return PERSONALITIES.get(key, PERSONALITIES["angry_boss"])

    def get_response(self, phone_count: int, context: str = "") -> str:
        import random
        return random.choice(self._personality()["prewritten_shame"])

    def get_praise(self) -> str:
        import random
        return random.choice(self._personality()["prewritten_praise"])


class TextToSpeech:
    """Generate WAV speech locally with Kokoro-82M."""

    _pipeline = None
    _model_lock = threading.Lock()

    def __init__(
        self,
        elevenlabs_key: str = "",
        voice: str = "",
        eleven_voice_id: str = "",
        personality: str = "mixtape",
    ):
        self.personality = personality
        self.model_id = os.getenv("KOKORO_MODEL_ID", "hexgrad/Kokoro-82M")
        self.language = os.getenv("KOKORO_LANGUAGE", "a")
        self.kokoro_voice = os.getenv("KOKORO_VOICE", "af_heart")
        self.speed = float(os.getenv("KOKORO_SPEED", "1.0"))
        if elevenlabs_key:
            logger.warning("Ignoring ElevenLabs key: this build is local-only.")

    @classmethod
    def _load_pipeline(cls, model_id: str, language: str, voice: str):
        with cls._model_lock:
            if cls._pipeline is not None:
                return cls._pipeline
            import torch
            from kokoro import KPipeline

            if torch.cuda.is_available():
                device = "cuda"
            elif torch.backends.mps.is_available():
                os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
                device = "mps"
            else:
                device = "cpu"

            logger.info("Loading local Kokoro-82M model on %s...", device)
            cls._pipeline = KPipeline(
                lang_code=language,
                repo_id=model_id,
                device=device,
            )
            # Voices are fetched lazily, so include the selected voice in preload.
            cls._pipeline.load_voice(voice)
            return cls._pipeline

    def preload(self):
        """Download and load Kokoro and its voice before the first request."""
        return self._load_pipeline(self.model_id, self.language, self.kokoro_voice)

    async def synthesize(self, text: str, output_path: str | None = None) -> str:
        """Generate a 24 kHz WAV locally without calling a cloud service."""
        if output_path is None:
            fd, output_path = tempfile.mkstemp(prefix="judgy_reachy_", suffix=".wav")
            os.close(fd)
        return await asyncio.to_thread(self._synthesize_sync, text, output_path)

    def _synthesize_sync(self, text: str, output_path: str) -> str:
        import numpy as np
        import soundfile as sf

        pipeline = self._load_pipeline(
            self.model_id, self.language, self.kokoro_voice
        )
        chunks = []
        for result in pipeline(
            text, voice=self.kokoro_voice, speed=self.speed
        ):
            if result.audio is not None:
                chunks.append(result.audio.detach().cpu().numpy())
        if not chunks:
            raise RuntimeError("Kokoro produced no audio")
        sf.write(output_path, np.concatenate(chunks), 24000)
        logger.info("Generated local Kokoro WAV: %s", output_path)
        return output_path
