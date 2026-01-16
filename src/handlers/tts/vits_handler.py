import threading
import requests
import random 
import string 

from requests_toolbelt.multipart.encoder import MultipartEncoder
from .tts import TTSHandler

class VitsHandler(TTSHandler):
    key = "vits"


    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.voices = tuple()
        voices = self.get_setting("voices")
        if voices is None or len(voices) == 0:
            threading.Thread(target=self.get_voices).start() 
        elif len(voices) > 0:
            self.voices = self.get_setting("voices")
    
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": "API Endpoint",
                "description": "URL of VITS API endpoint",
                "type": "entry",
                "default": "https://artrajz-vits-simple-api.hf.space/",
            },
            {   
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "default": "0",
            }

        ]

    def get_voices(self) -> tuple:
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        r = requests.get(endpoint + "/voice/speakers", timeout=10)
        if r.status_code == 200:
            js = r.json()
            result = tuple()
            for speaker in js["VITS"]:
                result += ((str(speaker["id"]) + "| " + speaker["name"] + " " + (str(speaker["lang"]) if len(speaker["lang"]) < 5 else "[Multi]"), str(speaker["id"])), )
            self.voices = result
            self.set_setting("voices", self.voices)
            return result 
        else:
            return tuple()

    def save_audio(self, message, file):
        self.voice_vits(message, file) 
    
    def voice_vits(self, text, filename, format="wav", lang="auto", length=1, noise=0.667, noisew=0.8, max=50):
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        id = self.get_setting("voice")
        fields = {
            "text": text,
            "id": str(id),
            "format": format,
            "lang": lang,
            "length": str(length),
            "noise": str(noise),
            "noisew": str(noisew),
            "max": str(max),
        }
        boundary = "----VoiceConversionFormBoundary" + "".join(
            random.sample(string.ascii_letters + string.digits, 16)
        )

        m = MultipartEncoder(fields=fields, boundary=boundary)
        headers = {"Content-Type": m.content_type}
        url = f"{endpoint}/voice"

        res = requests.post(url=url, data=m, headers=headers)
        path = filename

        with open(path, "wb") as f:
            f.write(res.content)
        return path
    
    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            self.set_setting("voices", tuple())
            threading.Thread(target=self.get_voices).start()


