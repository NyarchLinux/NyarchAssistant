import urllib.parse
import requests
import threading
from .translator import TranslatorHandler

class LigvaTranslateHandler(TranslatorHandler):
    key = "LigvaTranslate" 
    
    def __init__(self, settings, path: str):
        super().__init__(settings, path)
        self.languages = tuple()
        languages = self.get_setting("languages")
        if languages is not None and len(languages) > 0:
            self.languages = languages
        else:
            self.languages = tuple()
        if len(self.languages) == 0:
            threading.Thread(target=self.get_languages).start()

    def get_extra_settings(self) -> list:
        return [
            { "key": "endpoint", "title": "API Endpoint",
                "description": "URL of Ligva API endpoint",
                "type": "entry",
                "default": "https://lingva.dialectapp.org",
            },
            {
                "key": "language",
                "title": "Destination language",
                "description": "The language you want to translate to",
                "type": "combo",
                "values": self.languages,
                "default": "ja",
            }
        ]

    def get_languages(self):
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        r = requests.get(endpoint + "/api/v1/languages/", timeout=10)
        if r.status_code == 200:
            js = r.json()
            result = tuple()
            for language in js["languages"]:
                result += ((language["name"], language["code"]), )
            self.languages = result
            self.set_setting("languages", self.languages)
            return result
        else:
            return tuple()
    

    def translate(self, text: str) -> str:
        endpoint = self.get_setting("endpoint")
        endpoint = endpoint.rstrip("/")
        language = self.get_setting("language")
        response = requests.get(
            endpoint + "/api/v1/auto/" + urllib.parse.quote(language) + "/" + urllib.parse.quote(text),
        )
        if response.status_code != 200:
            return text
        return response.json()["translation"]

    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            threading.Thread(target=self.get_languages).start()

