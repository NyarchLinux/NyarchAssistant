import os
import json
import base64
import threading
import requests


from .image_generator import ImageGeneratorHandler
from ...handlers import ExtraSettings


class OpenRouterImageHandler(ImageGeneratorHandler):
    """Image generation handler for OpenRouter.

    Uses the OpenRouter Chat Completions endpoint with the ``modalities``
    parameter to generate images via image-capable models (e.g. Gemini,
    Flux, Recraft, Sourceful). The OpenRouter docs are at:
    https://openrouter.ai/docs/guides/overview/multimodal/image-generation
    """

    key = "openrouter-image"

    default_models = (
        ("google/gemini-2.5-flash-image", "google/gemini-2.5-flash-image"),
        ("black-forest-labs/flux.2-flex", "black-forest-labs/flux.2-flex"),
        ("black-forest-labs/flux.2-pro", "black-forest-labs/flux.2-pro"),
        ("sourceful/riverflow-v2-standard-preview", "sourceful/riverflow-v2-standard-preview"),
    )

    OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/"
    MODELS_URL = "https://openrouter.ai/api/v1/models"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.models = self.default_models
        cached = self.get_setting("models", False)
        if cached is not None:
            try:
                self.models = json.loads(cached)
            except Exception:
                pass
        else:
            threading.Thread(target=self._fetch_models, daemon=True).start()

    def _fetch_models(self):
        """Fetch image-capable models from OpenRouter's models API.

        Uses the ``output_modalities=image`` query parameter exposed by
        OpenRouter, which the OpenAI Python SDK does not forward.
        """
        api = self.get_setting("api")
        if not api:
            return
        try:
            resp = requests.get(
                self.MODELS_URL,
                params={"output_modalities": "image"},
                headers={
                    "Authorization": f"Bearer {api}",
                    **self.get_extra_headers(),
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            image_models = []
            for m in data.get("data", []):
                mid = m.get("id")
                if mid and not any(existing[0] == mid for existing in image_models):
                    image_models.append((mid, mid))
            if image_models:
                self.models = tuple(image_models)
                self.set_setting("models", json.dumps(self.models))
                print(self.models)
                self.settings_update()
        except Exception as e:
            print(f"Error fetching OpenRouter image models: {e}")

    def _refresh_models(self):
        threading.Thread(target=self._fetch_models, daemon=True).start()

    def get_extra_headers(self) -> dict:
        """OpenRouter-recommended attribution headers."""
        return {
            "HTTP-Referer": "https://github.com/qwersyk/Newelle",
            "X-Title": "Newelle",
        }

    @staticmethod
    def get_extra_requirements() -> list:
        return ["openai"]

    def get_extra_settings(self) -> list:
        return [
            ExtraSettings.EntrySetting(
                "api", _("API Key"),
                _("OpenRouter API key from openrouter.ai (required)"),
                "", password=True, website="https://openrouter.ai/keys",
            ),
            ExtraSettings.ComboSetting(
                "model", _("Model"),
                _("Image generation model. The model must have 'image' in its output_modalities."),
                self.models, self.default_models[0][0],
                refresh=lambda x: self._refresh_models(),
                website="https://openrouter.ai/models?output_modalities=image",
            ),
            ExtraSettings.MultilineEntrySetting(
                "positive-prompt", _("Positive Prompt Template"),
                _("Prompt template, [input] will be replaced with the user prompt"),
                "[input]",
            ),
            ExtraSettings.ComboSetting(
                "aspect_ratio", _("Aspect Ratio"),
                _("Aspect ratio of the generated image (model-dependent support)"),
                (
                    ("default", _("Default")),
                    ("1:1", _("1:1 (Square)")),
                    ("2:3", _("2:3 (Portrait)")),
                    ("3:2", _("3:2 (Landscape)")),
                    ("3:4", _("3:4 (Portrait)")),
                    ("4:3", _("4:3 (Landscape)")),
                    ("4:5", _("4:5 (Portrait)")),
                    ("5:4", _("5:4 (Landscape)")),
                    ("9:16", _("9:16 (Vertical)")),
                    ("16:9", _("16:9 (Wide)")),
                    ("21:9", _("21:9 (Ultra-wide)")),
                ),
                "default",
            ),
            ExtraSettings.ComboSetting(
                "image_size", _("Image Size"),
                _("Resolution of the generated image (model-dependent support)"),
                (
                    ("default", _("Default")),
                    ("0.5K", _("0.5K (Low)")),
                    ("1K", _("1K (Standard)")),
                    ("2K", _("2K (High)")),
                    ("4K", _("4K (Highest)")),
                ),
                "default",
            ),
        ]

    def generate_image(self, prompt: str, msg_uuid: str, output_file: str = None) -> str:
        """Generate an image using OpenRouter's Chat Completions endpoint.

        OpenRouter returns the generated image inside ``message.images`` as a
        base64 data URL, which we decode and save as a PNG to ``output_file``.
        """
        import openai
        from PIL import Image
        from io import BytesIO

        prompt = self.get_setting("positive-prompt").replace("[input]", prompt)
        output = output_file if output_file else os.path.join(self.cache_dir, f"{msg_uuid}.png")

        api = self.get_setting("api")
        if not api:
            raise RuntimeError(
                _("OpenRouter API key is not set. Configure it in the image generator settings.")
            )

        client = openai.OpenAI(api_key=api, base_url=self.OPENROUTER_ENDPOINT)

        model = self.get_setting("model")
        aspect_ratio = self.get_setting("aspect_ratio")
        image_size = self.get_setting("image_size")

        extra_body = {}
        image_config = {}
        if aspect_ratio and aspect_ratio != "default":
            image_config["aspect_ratio"] = aspect_ratio
        if image_size and image_size != "default":
            image_config["image_size"] = image_size
        if image_config:
            extra_body["image_config"] = image_config

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            #modalities=[m.strip() for m in modalities.split(",")],
            extra_body=extra_body or None,
            extra_headers=self.get_extra_headers(),
        )

        if not response.choices:
            raise RuntimeError(_("OpenRouter returned no choices in the response"))

        message = response.choices[0].message
        images = getattr(message, "images", None) or []
        if not images:
            raise RuntimeError(
                _("OpenRouter returned no images. The selected model may not support image generation.")
            )

        first = images[0]
        if isinstance(first, dict):
            image_url = first.get("image_url", {}).get("url")
        else:
            nested = getattr(first, "image_url", None)
            image_url = getattr(nested, "url", None) if nested is not None else None

        if not image_url:
            raise RuntimeError(_("OpenRouter returned an image without a URL"))

        if image_url.startswith("data:"):
            _, _, b64data = image_url.partition(",")
            img_bytes = base64.b64decode(b64data)
            img = Image.open(BytesIO(img_bytes))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            img.save(output, "PNG")
            return output

        return image_url
