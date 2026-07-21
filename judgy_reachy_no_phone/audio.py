"""Local response selection and local OmniVoice text-to-speech."""

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path

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
    """Generate WAV speech locally with OmniVoice and an approved voice sample."""

    _model = None
    _model_lock = threading.Lock()

    def __init__(
        self,
        elevenlabs_key: str = "",
        voice: str = "",
        eleven_voice_id: str = "",
        personality: str = "mixtape",
    ):
        self.personality = personality
        self.model_path = os.getenv("OMNIVOICE_MODEL_PATH", "k2-fsa/OmniVoice")
        self.reference_audio = Path(
            os.getenv(
                "OMNIVOICE_REFERENCE_AUDIO",
                Path(__file__).parent / "assets" / "voice_reference.wav",
            )
        )
        self.reference_text = os.getenv("OMNIVOICE_REFERENCE_TEXT", "")
        if elevenlabs_key:
            logger.warning("Ignoring ElevenLabs key: this build is local-only.")

    @classmethod
    def _load_model(cls, model_path: str):
        with cls._model_lock:
            if cls._model is not None:
                return cls._model
            import torch
            from omnivoice import OmniVoice

            if torch.cuda.is_available():
                device, dtype = "cuda:0", torch.float16
            elif torch.backends.mps.is_available():
                device, dtype = "mps", torch.float32
            else:
                device, dtype = "cpu", torch.float32

            logger.info("Loading local OmniVoice model on %s...", device)
            cls._model = OmniVoice.from_pretrained(
                model_path, device_map=device, dtype=dtype
            )
            return cls._model

    async def synthesize(self, text: str, output_path: str | None = None) -> str:
        """Generate a 24 kHz WAV locally without calling a cloud service."""
        if not self.reference_audio.is_file():
            raise FileNotFoundError(
                "Missing OmniVoice reference WAV: "
                f"{self.reference_audio}. See assets/README.md."
            )
        if not self.reference_text.strip():
            raise ValueError("Set OMNIVOICE_REFERENCE_TEXT to the exact words in the reference WAV.")
        if output_path is None:
            fd, output_path = tempfile.mkstemp(prefix="judgy_reachy_", suffix=".wav")
            os.close(fd)
        return await asyncio.to_thread(self._synthesize_sync, text, output_path)

    def _synthesize_sync(self, text: str, output_path: str) -> str:
        import soundfile as sf

        model = self._load_model(self.model_path)
        audio = model.generate(
            text=text,
            ref_audio=str(self.reference_audio),
            ref_text=self.reference_text,
        )
        sf.write(output_path, audio[0], 24000)
        logger.info("Generated local OmniVoice WAV: %s", output_path)
        return output_path
