import os 
from ...utility.pip import find_module, install_module
from .translator import TranslatorHandler

class GoogleTranslatorHandler(TranslatorHandler):
    key = "GoogleTranslator"
    
    def is_installed(self) -> bool:
        return find_module("googletranslate") is not None

    def install (self):
        install_module("git+https://github.com/ultrafunkamsterdam/googletranslate", self.pip_path)
        self.settings_update()

    def get_extra_settings(self) -> list:
        return [
            {
                "key": "language",
                "title": "Destination language",
                "description": "The language you want to translate to",
                "type": "combo",
                "values": self.get_languages(),
                "default": "ja",
            }
        ]

    def get_languages(self):
        if not self.is_installed():
            return []
        from googletranslate import LANG_CODE_TO_NAME
        result = []
        for lang_code in LANG_CODE_TO_NAME:
            result.append((LANG_CODE_TO_NAME[lang_code], lang_code))
        return result

    def translate(self, text: str) -> str:
        from googletranslate import translate
        dest = self.get_setting("language")
        return translate(text, dest)

