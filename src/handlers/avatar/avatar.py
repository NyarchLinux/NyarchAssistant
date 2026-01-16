from abc import abstractmethod
from typing import Any 
from gi.repository import Gtk
import os 
import threading 
import json 

from ...utility.strings import extract_expressions
from ..tts import TTSHandler
from ..handler import Handler

from ..translator import TranslatorHandler
class AvatarHandler(Handler):

    key : str = ""
    requires_reload : list = [False]
    lock : threading.Semaphore = threading.Semaphore(1)
    schema_key : str = "avatars"

    def __init__(self, settings, path: str):
        self.settings = settings
        self.path = path
        self.stop_request = False 

    def set_setting(self, key: str, value):
        """Set the given setting"""
        j = json.loads(self.settings.get_string(self.schema_key))
        if self.key not in j or not isinstance(j[self.key], dict):
            j[self.key] = {}
        j[self.key][key] = value
        self.requires_reload[0] = True
        self.settings.set_string(self.schema_key, json.dumps(j))


    @staticmethod
    def support_emotions() -> bool:
        return False
 
    @abstractmethod
    def create_gtk_widget(self) -> Gtk.Widget:
        """Create a GTK Widget to display the avatar"""
        pass

    @abstractmethod
    def get_expressions(self) -> list[str]:
        """Get the list of possible expressions"""
        pass

    @abstractmethod 
    def get_motions(self) -> list[str]:
        """Get the list of possible motions"""
        return []

    @abstractmethod 
    def set_expression(self, expression: str):
        """Set the expression"""
        pass

    @abstractmethod 
    def do_motion(self, motion: str):
        """Set the motion"""
        pass
    
    @abstractmethod
    def speak_with_tts(self, text: str, tts : TTSHandler, translator: TranslatorHandler):
        """ Speak the given text with the given TTS handler and Translation handler

        Args:
            text: Text to speak 
            tts: TTS handler 
            translator: Translation handler 
        """
        frame_rate = int(self.get_setting("fps", False, 10))
        chunks = extract_expressions(text, self.get_expressions() + self.get_motions()) 
        threads = []
        results = {}
        i = 0
        for chunk in chunks:
            if not chunk["text"].strip():
                t = threading.Thread(target=lambda : None)
                results[i] = {"filename": None, "expression": chunk["expression"]}
            else:
                t = threading.Thread(target=self.async_create_file, args=(chunk, tts, translator, frame_rate, i, results))
            t.start()
            threads.append(t)
            i+=1
        i = 0
        self.lock.acquire()
        for t in threads:
            t.join()
            if self.stop_request:
                self.lock.release()
                self.stop_request = False
                break
            result = results[i]
            if result["expression"] is not None:
                if result["expression"] in self.get_expressions():
                    self.set_expression(result["expression"])
                elif result["expression"] in self.get_motions():
                    self.do_motion(result["expression"])
            path = result["filename"]
            if path is not None:
                self.speak(path, tts, frame_rate)
            i+=1
        self.lock.release()

    @abstractmethod
    def speak(self, path: str, tts : TTSHandler, frame_rate: int):
        pass

    def destroy(self, add=None):
        """Destroy the widget"""
        pass

    def async_create_file(self, chunk: dict[str, str | None], tts : TTSHandler, translator : TranslatorHandler,frame_rate:int, id : int, results : dict[int, dict[ str, Any]]):
        """Function to be run on another thread - creates a file with the tts

        Args:
            chunk: chunk of the text to be spoken 
            tts: tts handler 
            translator: translation handler
            frame_rate: frame rate of the tts 
            id: id of the chunk 
            results: results of the chunks
        """
        filename = tts.get_tempname("wav")
        path = os.path.join(tts.path, filename)
        if chunk["text"] is None:
            return
        if translator is not None:
            chunk["text"] = translator.translate(chunk["text"])
        tts.save_audio(chunk["text"], path)
        results[id] = {
            "expression": chunk["expression"], 
            "filename": path,
        }

    def requires_reloading(self, handler) -> bool:
        """Check if the handler requires to be reloaded due to a settings change

        Args:
            handler (): new handler

        Returns:
            
        """
        if handler.key != self.key:
            return True
        if self.requires_reload[0]:
            self.requires_reload[0] = False
            return True
        return False

    def stop(self):
        """Stop the handler animations"""
        self.stop_request = True


