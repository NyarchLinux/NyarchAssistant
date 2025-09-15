import importlib.util 
import os 
import json 
import shutil
import sys
import inspect 

from gi.repository import Gtk, Adw

from .handlers import Handler
from .handlers.llm import LLMHandler
from .handlers.stt import STTHandler
from .handlers.tts import TTSHandler
from .handlers.rag import RAGHandler
from .handlers.memory import MemoryHandler
from .handlers.embeddings import EmbeddingHandler
from .handlers.websearch import WebSearchHandler
from .handlers.avatar import AvatarHandler
from .handlers.smart_prompt import SmartPromptHandler
from .handlers.translator import TranslatorHandler
from .ui_controller import UIController

class NewelleExtension(Handler):
    """The base class for all extensions"""
    
    # Name and ID of the extension
    # Name and ID must be less than 50 characters
    name = "Demo Extension"
    id = "demoextension"

    def __init__(self, pip_path : str, extension_path: str, settings):
        """
        Initialize the extension. No resource consuming action should be done on initialization

        Args:
            settings (): Gio application settings
            pip_path: path to the pip directory 
            extension_path: path to the extension cache directory 
        """
        self.pip_path = pip_path
        self.path = self.pip_path
        self.extension_path = extension_path
        self.settings = settings
        self.key = self.id
        self.schema_key = "extensions-settings"
        pass

    def set_handlers(self, llm: LLMHandler, stt: STTHandler, tts:TTSHandler|None, secondary_llm: LLMHandler, embedding: EmbeddingHandler, rag: RAGHandler|None, memory: MemoryHandler|None, websearch: WebSearchHandler):
        """Set the handlers for the extension

        Args:
            llm: LLMHandler 
            stt: STTHandler 
            tts: TTSHandler|None if disabled None is given 
            secondary_llm: LLMHandler (if disabled, is the same of LLMHandler) 
            embedding: EmbeddingHandler 
            rag: RAGHandler|None if disabled None is given 
            memory: MemoryHandler|None if disabled None is given 
        """
        self.llm = llm 
        self.stt = stt
        self.tts = tts
        self.secondary_llm = secondary_llm
        self.embedding = embedding
        self.rag = rag
        self.memory = memory
        self.websearch = websearch

    def get_llm_handlers(self) -> list[dict]:
        """
        Returns the list of LLM handlers

        Returns: 
            list: list of LLM handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": LLMHanlder - The class of the handler,
            } 
        """
        return [] 

    def get_tts_handlers(self) -> list[dict]:
        """
        Returns the list of TTS handlers

        Returns: 
            list: list of TTS handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": TTSHandler - The class of the handler,
            }
            
        """
        return [] 

    def get_stt_handlers(self) -> list[dict]:
        """
        Returns the list of STT handlers

        Returns:
            list: list of STT handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": STTHandler - The class of the handler,
            }
        """
        return [] 

    def get_memory_handlers(self) -> list[dict]:
        """
        Returns the list of memory handlers

        Returns:
            list: list of memory handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": MemoryHandler - The class of the handler,
            }
        """
        return []

    def get_embedding_handlers(self) -> list[dict]:
        """
        Returns the list of embedding handlers

        Returns:
            list: list of embedding handlers in this format
                "class": EmbeddingHandler - The class of the handler,
            }
        """
        return []

    def get_rag_handlers(self) -> list[dict]:
        """
        Returns the list of RAG handlers

        Returns:
            list: list of RAG handlers in this format 
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": RAGHandler - The class of the handler,
            }
        """
        return []
    def get_translators_handlers(self) -> list[dict]:
        """
        Returns the list of Translators handlers

        Returns:
            list: list of Translators handlers in this format
                "class": TranslatorHandler - The class of the handler,
            }
        """
        return [] 

    def get_avatar_handlers(self) -> list[dict]:
        """
        Returns the list of Avatar handlers

        Returns:
            list: list of Avatar handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": AvatarHandler - The class of the handler,
            }
        """
        return [] 

    def get_smart_prompts_handlers(self) -> list[dict]:
        """
        Returns the list of Smart Prompts handlers

        Returns:
            list: list of Smart Prompts handlers in this format
                "class": SmartPromptsHandler - The class of the handler,
            }
        """
        return []

    def get_websearch_handlers(self) -> list[dict]:
        """
        Returns the list of websearch handlers

        Returns:
            list: list of websearch handlers in this format
            {
                "key": "key of the handler",
                "title": "title of the handler",
                "description": "description of the handler",
                "class": WebSearchHandler - The class of the handler,
            }
        """
        return []
    
    def get_additional_prompts(self) -> list:
        """
        Returns the list of additional prompts

        Returns:
            list: list of additional prompts in this format
            {
                "key": "key of the prompt",
                "setting_name": "name of the settings that gets toggled",
                "title": "Title of the prompt to be shown in settings",
                "description": "Description of the prompt to be shown in settings",
                "editable": bool, If the user can edit the prompt
                "show_in_settings": bool If the prompt should be shown in the settings,
                "default": bool, default value of the setting
                "text": "Default Text of the prompt"
            }            
        """
        return []

    def get_replace_codeblocks_langs(self) -> list:
        """Get the list of codeblock langs that the extension handles and replaces

        Returns:
            list: list of codeblock langs that the extension handles and replaces 
        """
        return []

    def provides_both_widget_and_answer(self, codeblock: str, lang: str) -> bool:
        """
        Returns True if the extension provides both a widget and an answer

        Args:
            codeblock: str: text in the codeblock generated by the llm
            lang: str: language of the codeblock

        Returns:
            bool: True if the extension provides both a widget and an answer
        """
        return False

    def restore_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None: 
        """
        Restores a GTK widget on chat loading, optional, fallbacks to get_gtk_widget

        Args:
            codeblock: str: text in the codeblock generated by the llm
            lang: str: language of the codeblock
            msg_uuid: str: uuid of the message

        Returns:
            Gtk.Widget: widget to be shown in the chat or None if not provided 
        """
        # Retrocompatibility only, check if get_gtk_widget supports uuid 
        # there is no need to implement this in your extension 
        if len(inspect.signature(self.get_gtk_widget).parameters) == 2:
            return self.get_gtk_widget(codeblock, lang)
        return self.get_gtk_widget(codeblock, lang, msg_uuid)
    
    def get_gtk_widget(self, codeblock: str, lang: str, msg_uuid=None) -> Gtk.Widget | None: 
        """
        Returns the GTK widget to be shown in the chat, optional

        Args:
            codeblock: str: text in the codeblock generated by the llm
            lang: str: language of the codeblock
            msg_uuid: str: uuid of the message

        Returns:
            Gtk.Widget: widget to be shown in the chat or None if not provided 
        """
        return None

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        """
        Returns the answer to the codeblock

        Args:
            codeblock: str: text in the codeblock generated by the llm 
            lang: str: language of the codeblock 

        Returns:
            str: answer to the codeblock (will be given to the llm) or None if not provided
        """
        return None

    def preprocess_history(self, history: list, prompts : list) -> tuple[list, list]:
        """
        Called on the history before it is sent to the LLM. History is given in Newelle format
        """
        return history, prompts

    def postprocess_history(self, history: list, bot_response: str) -> tuple[list, str]:
        """
        Called on the history after it is received from the LLM. History is given in Newelle format
        """
        return history, bot_response

    def set_ui_controller(self, ui_controller: UIController):
        self.ui_controller = ui_controller
        
    def add_tab_menu_entries(self) -> list:
        """List of TabButtonDescriptions 

        Returns:
            list: List of TabButtonDescriptions to add to the add tab menu 
        """
        return []

class ExtensionLoader:
    """
    Class that loads the extensions

    Attributes: 
        extension_dir: directory where the extensions files are located 
        pip: path to the pip directory 
        extension_cache: path to the extension cache directory 
        settings: Gio application settings 
        extensions: list of extensions 
        disabled_extensions: list of disabled extensions 
        codeblocks: list of codeblocks and their corresponding extensions 
        filemap: map from extension id to file name 
    """
    def __init__(self, extension_dir, project_dir=None, pip_path="", extension_cache="", settings=None):
        self.extension_dir = extension_dir
        if project_dir is not None:
            self.project_dir = project_dir
        else:
            self.project_dir = os.path.dirname(os.path.abspath(__file__))
        self.pip = pip_path
        self.extension_cache = extension_cache
        self.settings = settings
    
        if self.settings is None:
            self.extensions_settings = {}
        else:
            self.extensions_settings = json.loads(self.settings.get_string("extensions-settings"))

        self.extensions : list[NewelleExtension] = []
        self.extensionsmap : dict[str, NewelleExtension] = {}
        self.disabled_extensions : list[NewelleExtension] = []
        self.codeblocks : dict[str, NewelleExtension] = {}
        self.filemap : dict[str, str] = {}

    def get_extensions(self) -> list[NewelleExtension]:
        return self.extensions

    def get_enabled_extensions(self) -> list[NewelleExtension]:
        return [x for x in self.get_extensions() if x not in self.disabled_extensions]

    def load_integrations(self, AVAILABLE_INTEGRATIONS):
        for integration in AVAILABLE_INTEGRATIONS:
            integration_class = integration(self.pip, self.extension_cache, self.settings)
            self.extensions_settings[integration_class.id] = {}
            self.save_settings()
            for lang in integration_class.get_replace_codeblocks_langs():
                if lang not in self.codeblocks:
                    self.codeblocks[lang] = integration_class
            self.extensions.append(integration_class)
            self.extensionsmap[integration_class.id] = integration_class

    def set_ui_controller(self, ui_controller):
        for extension in self.get_enabled_extensions():
            extension.set_ui_controller(ui_controller)
            
    def load_extensions(self):
        """Load extensions from the extension directory"""
        sys.path.insert(0, self.project_dir)
        for file in os.listdir(self.extension_dir):
            if file.endswith(".py"):
                try: 
                    spec = importlib.util.spec_from_file_location("nyarchassistant.name", os.path.join(self.extension_dir, file))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for class_name, class_obj in module.__dict__.items():
                        if isinstance(class_obj, type) and issubclass(class_obj, NewelleExtension) and class_obj != NewelleExtension:
                            extension = class_obj(self.pip, self.extension_cache, self.settings)
                            # Create entry in settings
                            if extension.id not in self.extensions_settings:
                                self.extensions_settings[extension.id] = {}
                                self.save_settings()

                            # Save properties about enabled and codeblocks
                            if extension.id in self.extensions_settings and ("disabled" not in self.extensions_settings[extension.id] or not self.extensions_settings[extension.id]["disabled"]):
                                for lang in extension.get_replace_codeblocks_langs():
                                    if lang not in self.codeblocks:
                                        self.codeblocks[lang] = extension
                            else:
                                self.disabled_extensions.append(extension)
                            self.extensions.append(extension)
                            self.filemap[extension.id] = file
                            self.extensionsmap[extension.id] = extension
                            break
                except Exception as e:
                    print("Error loding file: ", file, e)
            
        sys.path.remove(self.project_dir)

    def set_handlers(self, llm: LLMHandler, stt: STTHandler, tts:TTSHandler|None, secondary_llm: LLMHandler, embedding: EmbeddingHandler, rag: RAGHandler|None, memory: MemoryHandler|None, websearch: WebSearchHandler | None):
        for extension in self.extensions:
            extension.set_handlers(llm, stt, tts, secondary_llm, embedding, rag, memory, websearch)

    def add_handlers(self, AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAG, AVAILABLE_WEBSEARCH, AVAILABLE_AVATARS, AVAILABLE_TRANSLATORS, AVAILABLE_SMART_PROMPTS):
        """Add the handlers of each extension to the available handlers

        Args:
            AVAILABLE_LLMS (): list of available llms 
            AVAILABLE_TTS (): list of available tts
            AVAILABLE_STT (): list of available stt
            AVAILABLE_MEMORIES (): list of available memories
            AVAILABLE_EMBEDDINGS (): list of available embeddings
            AVAILABLE_RAG (): list of available rags
            AVAILABLE_WEBSEARCH (): list of available websearch
        """
        for extension in self.extensions:
            if extension in self.disabled_extensions:
                continue
            handlers = extension.get_llm_handlers()
            for handler in handlers:
                AVAILABLE_LLMS[handler["key"]] = handler
            handlers = extension.get_tts_handlers()
            for handler in handlers:
                AVAILABLE_TTS[handler["key"]] = handler
            handlers = extension.get_stt_handlers()
            for handler in handlers:
                AVAILABLE_STT[handler["key"]] = handler
            handlers = extension.get_memory_handlers()
            for handler in handlers:
                AVAILABLE_MEMORIES[handler["key"]] = handler
            handlers = extension.get_embedding_handlers()
            for handler in handlers:
                AVAILABLE_EMBEDDINGS[handler["key"]] = handler
            handlers = extension.get_rag_handlers()
            for handler in handlers:
                AVAILABLE_RAG[handler["key"]] = handler
            handlers = extension.get_websearch_handlers()
            for handler in handlers:
                AVAILABLE_WEBSEARCH[handler["key"]] = handler
            handler = extension.get_translators_handlers()
            for h in handler:
                AVAILABLE_TRANSLATORS[h["key"]] = h
            handler = extension.get_smart_prompts_handlers()
            for h in handler:
                AVAILABLE_SMART_PROMPTS[h["key"]] = h
            handler = extension.get_avatar_handlers()
            for h in handler:
                AVAILABLE_AVATARS[h["key"]] = h


    def add_prompts(self, PROMPTS, AVAILABLE_PROMPTS):
        """Add the prompts of each extension to the available prompts

        Args:
            PROMPTS (): the prompts texts list 
            AVAILABLE_PROMPTS (): the available prompts list 
        """
        for extension in self.extensions:
            if extension in self.disabled_extensions:
                continue
            prompts = extension.get_additional_prompts()
            for prompt in prompts:
                if prompt not in AVAILABLE_PROMPTS:
                    AVAILABLE_PROMPTS.append(prompt)
                PROMPTS[prompt["key"]] = prompt["text"]

    def remove_handlers(self, extension, AVAILABLE_LLMS, AVAILABLE_TTS, AVAILABLE_STT, AVAILABLE_MEMORIES, AVAILABLE_EMBEDDINGS, AVAILABLE_RAG, AVAILABLE_WEBSEARCH, AVAILABLE_AVATARS, AVAILABLE_TRANSLATORS, AVAILABLE_SMART_PROMPTS):
        """Remove handlers of an extension

        Args:
            AVAILABLE_LLMS (): list of available llms 
            AVAILABLE_TTS (): list of available tts
            AVAILABLE_STT (): list of available stt
        """
        handlers = extension.get_llm_handlers()
        for handler in handlers:
            AVAILABLE_LLMS.pop(handler["key"])
        handlers = extension.get_tts_handlers()
        for handler in handlers:
            AVAILABLE_TTS.pop(handler["key"])
        handlers = extension.get_stt_handlers()
        for handler in handlers:
            AVAILABLE_STT.pop(handler["key"])
        handlers = extension.get_memory_handlers()
        for handler in handlers:
            AVAILABLE_MEMORIES.pop(handler["key"])
        handlers = extension.get_embedding_handlers()
        for handler in handlers:
            AVAILABLE_EMBEDDINGS.pop(handler["key"])
        handlers = extension.get_rag_handlers()
        for handler in handlers:
            AVAILABLE_RAG.pop(handler["key"])
        handlers = extension.get_websearch_handlers()
        for handler in handlers:
            AVAILABLE_WEBSEARCH.pop(handler["key"])
        handler = extension.get_translators_handlers()
        for h in handler:
            AVAILABLE_TRANSLATORS.pop(h["key"])
        handler = extension.get_smart_prompts_handlers()
        for h in handler:
            AVAILABLE_SMART_PROMPTS.pop(h["key"])
        handler = extension.get_avatar_handlers()
        for h in handler:
            AVAILABLE_AVATARS.pop(h["key"])

    def remove_prompts(self, extension, PROMPTS, AVAILABLE_PROMPTS):
        """Remove prompts of an extension

        Args:
            PROMPTS (): the prompts texts list 
            AVAILABLE_PROMPTS (): the available prompts list 
        """
        prompts = extension.get_additional_prompts()
        for prompt in prompts:
            if prompt in AVAILABLE_PROMPTS:
                AVAILABLE_PROMPTS.remove(prompt)
            PROMPTS.pop(prompt["key"])

    def remove_extension(self, extension : NewelleExtension | str):
        """
        Remove an extension - deletes the file

        Args:
            extension: the extension to remove 
        """
        if not isinstance(extension, str):
            extension = extension.id
        os.remove(os.path.join(self.extension_dir, self.filemap[extension]))

    def add_extension(self, file_path : str):
        """
        Add an extension - copies the file

        Args:
            file_path: the path of the file to copy 
        """
        print(self.extension_dir)
        shutil.copyfile(file_path, os.path.join(self.extension_dir, os.path.basename(file_path)))
        #os.makedirs(os.path.join(self.extension_cache, os.path.basename(file_path)), exist_ok=True)

    def get_extension_by_id(self, id: str) -> NewelleExtension | None:
        """
        Get an extension by its id

        Args:
            id: the id of the extension 

        Returns:
            NewelleExtension | None: the extension or None if not found 
        """
        if id in self.extensionsmap:
            return self.extensionsmap[id]
        return None

    def enable(self, extension : NewelleExtension | str):
        """
        Enable an extension

        Args:
            extension: the extension to enable 
        """
        if not isinstance(extension, str):
            extension = extension.id
        if self.get_extension_by_id(extension) in self.disabled_extensions:
            self.disabled_extensions.remove(self.get_extension_by_id(extension))
        self.extensions_settings[extension]["disabled"] = False
        self.save_settings()

    def disable(self, extension : NewelleExtension | str): 
        """
        Disable an extension

        Args:
            extension: the extension to disable 
        """
        if not isinstance(extension, str):
            extension = extension.id
        self.extensions_settings[extension]["disabled"] = True
        self.disabled_extensions.append(self.get_extension_by_id(extension))
        self.save_settings()

    def save_settings(self):
        """Save the extensions settings"""
        if self.settings is None:
            return
        self.settings.set_string("extensions-settings", json.dumps(self.extensions_settings))
    
    def check_validity(self, extension : NewelleExtension):
        """
        Check if the extension is valid

        Args:
            extension: the extension to check 

        Returns:
            bool: True if valid, False otherwise 
        """
        if not hasattr(extension, "id") or not hasattr(extension, "name") or len(extension.id) > 50 or len(extension.name) > 50 or extension.id == NewelleExtension.id or extension.name == NewelleExtension.name:
            print("Error: invalid extension, missing id or name")
            return False
        for h in extension.get_llm_handlers():
            if not self.check_handler(h, LLMHandler):
                return False
        for h in extension.get_tts_handlers():
            if not self.check_handler(h, TTSHandler):
                return False
        for h in extension.get_stt_handlers():
            if not self.check_handler(h, STTHandler):
                return False
        for h in extension.get_avatar_handlers():
            if not self.check_handler(h, AvatarHandler):
                return False
        for h in extension.get_smart_prompts_handlers():
            if not self.check_handler(h, SmartPromptHandler):
                return False
        for h in extension.get_translators_handlers():
            if not self.check_handler(h, TranslatorHandler):
                return False


        for p in extension.get_additional_prompts():
            if not self.check_prompt(p):
                return False
        return True

    def check_handler(self, handler : dict, compare):
        if "key" not in handler or "title" not in handler or "description" not in handler or "class" not in handler:
            print("Error: invalid handler, missing key or title or description or class")
            return False
        if not issubclass(handler["class"], compare):
            print("Error: invalid handler, class does not match")
            return False
        return True

    def check_prompt(self, prompt):
        if "key" not in prompt or "setting_name" not in prompt or "title" not in prompt or "description" not in prompt:
            print("Error: invalid prompt, missing key or setting_name or title or description")
            return False
        return True

    def preprocess_history(self, history: list, prompts : list) -> tuple[list, list]:
        """
        Called on the history before it is sent to the LLM. History is given in Newelle format
        """
        for extension in self.get_enabled_extensions():
            try:
                history, prompts = extension.preprocess_history(history, prompts)
            except Exception as e:
                print(e)
        return history, prompts

    def postprocess_history(self, history: list, bot_response: str) -> tuple[list, str]:
        """
        Called on the history after it is received from the LLM. History is given in Newelle format
        """
        for extension in self.get_enabled_extensions():
            try:
                history, bot_response = extension.postprocess_history(history, bot_response)
            except Exception as e:
                print(e)
        return history, bot_response

    def get_add_tab_buttons(self) -> list:
        buttons = []
        for extension in self.get_enabled_extensions():
            try:
                buttons += extension.add_tab_menu_entries()
            except Exception as e:
                print(e)
        return buttons
