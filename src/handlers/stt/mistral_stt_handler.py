import os

from .stt import STTHandler
from ...utility.pip import install_module, find_module
from ...handlers import ErrorSeverity, ExtraSettings


SUPPORTED_MODELS = ("voxtral-mini-latest", "voxtral-mini-2507")


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


def _get_mistral_file_class():
    """Return the SDK's ``File`` upload class (path varies across versions)."""
    try:
        from mistralai.client.models import File
    except ImportError:
        from mistralai.models import File  # legacy v1 layout
    return File


class MistralSTTHandler(STTHandler):
    """Voxtral speech recognition via the Mistral API.

    Docs: https://docs.mistral.ai/studio-api/audio/speech_to_text
    """

    key = "mistral_sr"

    def get_extra_settings(self) -> list:
        models = tuple((m, m) for m in SUPPORTED_MODELS)
        return [
            ExtraSettings.EntrySetting(
                "api", _("API Key"),
                _("Mistral API key, get one at console.mistral.ai"),
                "", website="https://console.mistral.ai/", password=True,
            ),
            ExtraSettings.ComboSetting(
                "model", _("Model"), _("The transcription model to use"),
                models, SUPPORTED_MODELS[0],
                website="https://docs.mistral.ai/models/model-cards/voxtral-mini-2507",
            ),
            ExtraSettings.EntrySetting(
                "language", _("Language"),
                _("Optional: language of the audio to boost accuracy, as an ISO 639-1 code (e.g. \"en\", \"fr\")"),
                "",
            ),
            ExtraSettings.ToggleSetting(
                "diarize", _("Speaker diarization"),
                _("Label different speakers in the transcription"),
                False,
            ),
        ]

    def install(self):
        install_module("mistralai", self.pip_path)
        if not self.is_installed():
            self.throw("Mistral SDK installation failed", ErrorSeverity.ERROR)
        self._is_installed_cache = None

    def is_installed(self) -> bool:
        return find_module("mistralai") is not None

    def recognize_file(self, path) -> str | None:
        try:
            api_key = self.get_setting("api")
            client = _get_mistral_client(api_key)
            MistralFile = _get_mistral_file_class()

            with open(path, "rb") as audio_file:
                content = audio_file.read()
            uploaded = MistralFile(file_name=os.path.basename(path), content=content)

            kwargs = {
                "model": self.get_setting("model") or SUPPORTED_MODELS[0],
                "file": uploaded,
            }
            language = self.get_setting("language")
            if language:
                kwargs["language"] = language
            if self.get_setting("diarize"):
                kwargs["diarize"] = True

            response = client.audio.transcriptions.complete(**kwargs)
            return response.text
        except Exception as e:
            self.throw(f"Mistral STT error: {e}", ErrorSeverity.ERROR)
            return None
