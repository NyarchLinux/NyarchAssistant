from subprocess import check_output
from ...utility.system import get_spawn_command 
from .translator import TranslatorHandler

class CustomTranslatorHandler(TranslatorHandler):
    key="CustomTranslator"

 
    @staticmethod
    def requires_sandbox_escape() -> bool:
        """If the handler requires to run commands on the user host system"""
        return True

    def get_extra_settings(self) -> list:
        return [{
            "key": "command",
            "title": _("Command to execute"),
            "description": _("{0} will be replaced with the text to translate"),
            "type": "entry",
            "default": ""
        }]

    def is_installed(self):
        return True

    def translate(self, text: str) -> str:
        command = self.get_setting("command")
        if command is not None:
            value = check_output(get_spawn_command() + ["bash", "-c", command.replace("{0}", text)])
            return value.decode("utf-8")
        return text

