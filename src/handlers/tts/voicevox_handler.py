import threading 
import asyncio

from ...utility.force_sync import force_sync
from .tts import TTSHandler

class VoiceVoxHanlder(TTSHandler):
    key = "voicevox"

    def __init__(self, settings, path):
        super().__init__(settings, path)
        self._loop = asyncio.new_event_loop()
        self._thr = threading.Thread(target=self._loop.run_forever, name="Async Runner", daemon=True)
        self.voices = tuple()
        voices = self.get_setting("voices")
        if not voices:
            threading.Thread(target=self.get_voices).start() 
        else:
            self.voices = self.get_setting("voices")

    def update_voices(self):
        if self.get_setting("voices") is None or len(self.get_setting("voices")) == 0:
            threading.Thread(target=self.get_voices).start()
    
    def get_extra_settings(self) -> list:
        return [
            {
                "key": "endpoint",
                "title": "API Endpoint",
                "description": "URL of VoiceVox API endpoint",
                "type": "entry",
                "default": "https://meowskykung-voicevox-engine.hf.space",
            },
            {
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "default": "1",
            }
        ]

    def save_audio(self, message, file):
        from voicevox import Client

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        speaker = int(self.get_setting("voice"))
        endpoint = self.get_setting("endpoint")
        @force_sync
        async def save(message, speaker, endpoint):
            async with Client(base_url=endpoint) as client:
                audioquery = await client.create_audio_query(message, speaker=speaker)
                with open(file, "wb") as f:
                    f.write(await audioquery.synthesis(speaker=speaker))
        _ = save(message, speaker, endpoint)

    def get_voices(self) -> tuple:
        from voicevox import Client

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        endpoint = self.get_setting("endpoint")
        @force_sync
        async def get_voices(endpoint):
            ret = tuple()
            async with Client(base_url=endpoint) as client:
                speakers = await client.fetch_speakers()
                i = 1
                for speaker in speakers:
                    ret+= ((speaker.name, i), )
                    i+=1
            self.voices = ret
        _ = get_voices(endpoint)
        self.set_setting("voices", self.voices)
        return self.voices

    def set_setting(self, setting, value):
        super().set_setting(setting, value)
        if setting == "endpoint":
            self.set_setting("voices", tuple())
            threading.Thread(target=self.get_voices).start()

