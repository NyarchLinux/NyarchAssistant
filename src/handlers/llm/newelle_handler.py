import json

from typing import Any 
from typing import Callable
from .openai_handler import OpenAIHandler

class NewelleAPIHandler(OpenAIHandler):
    key = "newelle"
    error_message = """Error calling Newelle API. Please note that Newelle API is **just for demo purposes.**\n\nTo know how to use a more reliable LLM [read our guide to llms](https://github.com/qwersyk/newelle/wiki/User-guide-to-the-available-LLMs). \n\nError: """

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.set_setting("endpoint", "https://llm.nyarchlinux.moe")
        self.set_setting("advanced_params", False)
        self.set_setting("api", "newelle")

    def get_models_list(self):
        return [("Medium", "Medium"), ("Smart", "Smart"), ("Small", "Small")]

    def get_models(self, *args):
        pass
    
    def get_extra_settings(self) -> list:
        return self.build_extra_settings("Newelle",False, True, False, False, False, None, None, False, False)

    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args: list = []) -> str:
        model = self.get_setting("model", True, "Medium")
        if prompt.startswith("/chatname") or model == "Small":
            self.set_setting("endpoint", "https://llm.nyarchlinux.moe/small")
        elif prompt.startswith("```image") or  any(message.get("Message", "").startswith("```image") for message in history):
            self.set_setting("endpoint", "https://llm.nyarchlinux.moe/vision")
            print("Using nyarch vision...")
        elif model == "Smart":
            self.set_setting("endpoint", "https://llm.nyarchlinux.moe/smart")
        else:
            self.set_setting("endpoint", "https://llm.nyarchlinux.moe/")
        return super().generate_text_stream(prompt, history, system_prompt, on_update, extra_args)

    def generate_chat_name(self, request_prompt: str = "", history: list = []) -> str | None:
        """Generate name of the current chat

        Args:
            request_prompt (str, optional): Extra prompt to generate the name. Defaults to None.

        Returns:
            str: name of the chat
        """
        request_prompt = "/chatname" + request_prompt
        return super().generate_chat_name(request_prompt, history)
