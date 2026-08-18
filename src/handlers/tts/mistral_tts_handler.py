import json
import os
import base64

from .tts import TTSHandler
from ...utility.pip import install_module, find_module
from ...handlers import ErrorSeverity, ExtraSettings


def _get_mistral_client(api_key):
    """Create a Mistral SDK client.

    The import path changed across SDK majors, so support both:
    - ``mistralai>=2``: ``from mistralai.client import Mistral``
    - ``mistralai<2``: ``from mistralai import Mistral``
    """
    try:
        from mistralai.client import Mistral
    except ImportError:
        from mistralai import Mistral
    return Mistral(api_key=api_key)


# Known preset voices for Voxtral Mini TTS (20 presets across 9 languages).
# Used as a fallback when the API key is not set yet or the voices list call
# fails. The API exposes ``GET /v1/audio/voices`` to fetch them dynamically.
PRESET_VOICES = (
    ("Neutral female (en)", "neutral_female"),
    ("Neutral male (en)", "neutral_male"),
    ("Casual female (en)", "casual_female"),
    ("Casual male (en)", "casual_male"),
    ("Cheerful female (en)", "cheerful_female"),
    ("Paul neutral (en)", "en_paul_neutral"),
    ("Female (fr)", "fr_female"),
    ("Male (fr)", "fr_male"),
    ("Female (de)", "de_female"),
    ("Male (de)", "de_male"),
    ("Female (es)", "es_female"),
    ("Male (es)", "es_male"),
    ("Female (it)", "it_female"),
    ("Male (it)", "it_male"),
    ("Female (pt)", "pt_female"),
    ("Male (pt)", "pt_male"),
    ("Female (nl)", "nl_female"),
    ("Male (nl)", "nl_male"),
    ("Male (ar)", "ar_male"),
    ("Female (hi)", "hi_female"),
    ("Male (hi)", "hi_male"),
)

SUPPORTED_MODELS = ("voxtral-mini-tts-2603",)
SUPPORTED_FORMATS = ("mp3", "wav", "pcm")

# Audio extensions that may be used as a reference clip for voice cloning.
CLONE_AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac")


class MistralTTSHandler(TTSHandler):
    """Voxtral Mini TTS via the Mistral API.

    Docs: https://docs.mistral.ai/studio-api/audio/text_to_speech
    """

    key = "mistral_tts"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        # Cache for reference-audio base64 encoding, keyed by (path, mtime, size)
        self._ref_audio_cache = None
        # Dedicated directory where the user drops reference audio clips for
        # zero-shot voice cloning. Created lazily.
        self.voice_clones_dir = os.path.join(self.path, "voice_clones")
        if not os.path.exists(self.voice_clones_dir):
            try:
                os.makedirs(self.voice_clones_dir)
            except Exception:
                pass

    def get_clone_samples(self, manual: bool = False) -> tuple:
        """List reference audio clips placed in the voice-clones directory.

        Returns ``(display_name, absolute_path)`` tuples for every audio file
        found. Mirrors the ``get_wakeword_models`` pattern in
        ``openwakeword_handler``.
        """
        samples = tuple()
        if os.path.isdir(self.voice_clones_dir):
            for fname in sorted(os.listdir(self.voice_clones_dir)):
                if fname.lower().endswith(CLONE_AUDIO_EXTENSIONS):
                    name = os.path.splitext(fname)[0]
                    samples += ((name, os.path.join(self.voice_clones_dir, fname)),)
        if manual:
            self.settings_update()
        return samples

    def install(self):
        install_module("mistralai", self.pip_path)
        if not self.is_installed():
            self.throw("Mistral SDK installation failed", ErrorSeverity.ERROR)
        self._is_installed_cache = None

    def is_installed(self) -> bool:
        return find_module("mistralai") is not None

    def get_extra_settings(self) -> list:
        voices = self.get_voices()
        default_voice = voices[0][1] if voices else PRESET_VOICES[0][1]
        models = tuple((m, m) for m in SUPPORTED_MODELS)
        formats = tuple((f, f) for f in SUPPORTED_FORMATS)
        cloning_enabled = self.get_setting("voice_cloning", False)

        r = [
            ExtraSettings.EntrySetting(
                "api_key", _("API Key"),
                _("Mistral API key, get one at console.mistral.ai"),
                "", website="https://console.mistral.ai/", password=True,
            ),
            ExtraSettings.ToggleSetting(
                "voice_cloning", _("Voice cloning"),
                _("Clone a voice from a short reference audio clip (zero-shot) instead of using a preset voice"),
                False, website="https://docs.mistral.ai/studio-api/audio/text_to_speech",
                update_settings=True,
            ),
        ]
        if cloning_enabled:
            # When cloning is enabled we pass ``ref_audio`` and omit ``voice_id``.
            # Reference clips are dropped into the dedicated voice_clones
            # directory (open it with the folder button) and picked from here.
            samples = self.get_clone_samples()
            default_sample = samples[0][1] if samples else ""
            r += [
                ExtraSettings.ComboSetting(
                    "clone_sample", _("Reference voice"), _("Pick a reference audio clip to clone"),
                    samples, default_sample,
                    folder=self.voice_clones_dir,
                    refresh=lambda button: self.get_clone_samples(True),
                    update_settings=True,
                ),
            ]
        else:
            r += [
                ExtraSettings.ComboSetting(
                    "voice", _("Voice"), _("The preset or custom voice to use"),
                    voices, default_voice, update_settings=True,
                    refresh=lambda button: self.get_voices(True),
                ),
            ]
        r += [
            ExtraSettings.ComboSetting(
                "model", _("Model"), _("The TTS model to use"),
                models, SUPPORTED_MODELS[0],
                website="https://docs.mistral.ai/models/model-cards/voxtral-tts-26-03",
            ),
            ExtraSettings.ComboSetting(
                "response_format", _("Response format"), _("Audio format returned by the API"),
                formats, "mp3", update_settings=True,
            ),
            ExtraSettings.ToggleSetting(
                "streaming", _("Streaming"),
                _("Stream audio as it is generated (lower latency). Disable if you get playback issues."),
                True,
            ),
        ]
        return r

    def _get_ref_audio(self) -> str | None:
        """Return the base64-encoded reference audio, or None if not set/invalid.

        The selected clone sample (an absolute path from the combo row) is read
        and base64-encoded, as expected by Voxtral's ``ref_audio`` parameter.
        The encoding is cached keyed by (path, mtime, size) so we don't re-read
        and re-encode the file on every synthesis call.
        """
        path = self.get_setting("clone_sample", search_default=False)
        if not path:
            return None
        try:
            mtime = os.path.getmtime(path)
            size = os.path.getsize(path)
        except OSError:
            self.throw(
                _("Voice cloning: reference audio file not found or unreadable"),
                ErrorSeverity.WARNING,
            )
            return None
        cache = self._ref_audio_cache
        if cache and cache[0] == (path, mtime, size):
            return cache[1]
        try:
            with open(path, "rb") as f:
                data = f.read()
            encoded = base64.b64encode(data).decode("ascii")
            self._ref_audio_cache = ((path, mtime, size), encoded)
            return encoded
        except Exception as e:
            self.throw(f"Voice cloning: could not read reference audio: {e}", ErrorSeverity.WARNING)
            return None

    def _build_speech_kwargs(self, message):
        """Build the kwargs dict for ``client.audio.speech.complete``."""
        kwargs = {
            "model": self.get_setting("model") or SUPPORTED_MODELS[0],
            "input": message,
            "response_format": self.get_setting("response_format") or "mp3",
        }
        if self.get_setting("voice_cloning", False):
            ref_audio = self._get_ref_audio()
            if ref_audio:
                kwargs["ref_audio"] = ref_audio
            else:
                # Fall back to the selected voice if no usable reference clip
                kwargs["voice_id"] = self.get_current_voice()
        else:
            kwargs["voice_id"] = self.get_current_voice()
        return kwargs

    def get_voices(self, manual: bool = False) -> tuple:
        """Return the available voices as (display_name, voice_id) tuples.

        Fetches presets + custom voices from the API and caches the result.
        Falls back to the built-in ``PRESET_VOICES`` when no API key is set or
        the request fails.
        """
        # Read the key without falling back to defaults: get_default_setting()
        # rebuilds get_extra_settings(), which calls get_voices() and would
        # otherwise recurse. An unset key simply means "not configured yet".
        api_key = self.get_setting("api_key", search_default=False)
        cached = None
        try:
            cached = json.loads(self.get_setting("voices", False))
        except Exception:
            cached = None
        if cached and not manual:
            return tuple(tuple(v) for v in cached)

        if not api_key:
            return PRESET_VOICES

        try:
            client = _get_mistral_client(api_key)
            result = tuple()
            try:
                response = client.audio.voices.list(type_="all")
                items = getattr(response, "items", None) or []
                for voice in items:
                    voice_id = getattr(voice, "voice_id", None) or getattr(voice, "id", None)
                    if not voice_id:
                        continue
                    name = getattr(voice, "name", None) or voice_id
                    result += ((name, voice_id),)
            except Exception as e:
                if manual:
                    self.throw("Error getting Mistral voices: " + str(e), ErrorSeverity.WARNING)
                print("Error getting Mistral voices: " + str(e))

            if not result:
                result = PRESET_VOICES
            self.set_setting("voices", json.dumps(result))
            if manual:
                self.settings_update()
            return result
        except Exception as e:
            if manual:
                self.throw("Error getting Mistral voices: " + str(e), ErrorSeverity.WARNING)
            print("Error getting Mistral voices: " + str(e))
            return cached if cached else PRESET_VOICES

    def save_audio(self, message, file):
        try:
            client = _get_mistral_client(self.get_setting("api_key"))
            kwargs = self._build_speech_kwargs(message)
            res = client.audio.speech.complete(**kwargs)

            # ``complete`` returns a SpeechResponse when stream is not requested
            # and an event stream otherwise. Handle both shapes defensively.
            audio_data = self._extract_audio(res)
            with open(file, "wb") as f:
                f.write(audio_data)
        except Exception as e:
            self.throw(f"Mistral TTS error: {e}", ErrorSeverity.ERROR)

    @staticmethod
    def _extract_audio(response) -> bytes:
        """Decode the audio bytes from a speech response.

        Handles three response shapes:
        - ``SpeechResponse`` with a base64 ``audio_data`` string.
        - An iterable of SSE events (``event == "speech.audio.delta"``).
        - Raw bytes.
        """
        # Plain bytes / file-like with read()
        if isinstance(response, (bytes, bytearray)):
            return bytes(response)
        # Object holding base64 audio_data (non-streaming response)
        audio_data = getattr(response, "audio_data", None)
        if isinstance(audio_data, (bytes, bytearray)):
            return bytes(audio_data)
        if isinstance(audio_data, str):
            return base64.b64decode(audio_data)

        # Streaming response: an iterable of events
        try:
            chunks = []
            for event in response:
                etype = getattr(event, "event", None)
                data = getattr(event, "data", None)
                if etype == "speech.audio.delta":
                    delta = getattr(data, "audio_data", None)
                    if delta:
                        chunks.append(base64.b64decode(delta))
            if chunks:
                return b"".join(chunks)
        except Exception:
            pass

        raise ValueError("Could not extract audio data from Mistral response")

    def streaming_enabled(self) -> bool:
        return bool(self.get_setting("streaming"))

    def get_stream_format_args(self) -> list:
        fmt = self.get_setting("response_format") or "mp3"
        return ["-f", fmt]

    def get_audio_stream(self, message):
        """Yield raw audio bytes for *message* using the streaming endpoint."""
        import base64
        client = _get_mistral_client(self.get_setting("api_key"))
        kwargs = self._build_speech_kwargs(message)
        kwargs["stream"] = True
        res = client.audio.speech.complete(**kwargs)
        # When stream=True the SDK returns an event stream we can iterate.
        if hasattr(res, "audio_data"):
            yield base64.b64decode(res.audio_data)
            return
        try:
            for event in res:
                if getattr(event, "event", None) == "speech.audio.delta":
                    data = getattr(event, "data", None)
                    delta = getattr(data, "audio_data", None) if data else None
                    if delta:
                        yield base64.b64decode(delta)
        except Exception as e:
            self.throw(f"Mistral TTS streaming error: {e}", ErrorSeverity.WARNING)
