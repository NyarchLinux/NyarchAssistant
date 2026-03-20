from abc import abstractmethod
from ..handler import Handler

class TranslatorHandler(Handler):
    key = ""
    schema_key = "translator-settings"    
    
    @abstractmethod
    def translate(self, text: str) -> str:
        return text

