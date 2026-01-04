import threading 
import asyncio
import json
from ...utility.force_sync import force_sync
from .tts import TTSHandler

VOICES = """
[["\u56db\u56fd\u3081\u305f\u3093", 1], ["\u305a\u3093\u3060\u3082\u3093", 2], ["\u6625\u65e5\u90e8\u3064\u3080\u304e", 3], ["\u96e8\u6674\u306f\u3046", 4], ["\u6ce2\u97f3\u30ea\u30c4", 5], ["\u7384\u91ce\u6b66\u5b8f", 6], ["\u767d\u4e0a\u864e\u592a\u90ce", 7], ["\u9752\u5c71\u9f8d\u661f", 8], ["\u51a5\u9cf4\u3072\u307e\u308a", 9], ["\u4e5d\u5dde\u305d\u3089", 10], ["\u3082\u3061\u5b50\u3055\u3093", 11], ["\u5263\u5d0e\u96cc\u96c4", 12], ["WhiteCUL", 13], ["\u5f8c\u9b3c", 14], ["No.7", 15], ["\u3061\u3073\u5f0f\u3058\u3044", 16], ["\u6afb\u6b4c\u30df\u30b3", 17], ["\u5c0f\u591c/SAYO", 18], ["\u30ca\u30fc\u30b9\u30ed\u30dc\uff3f\u30bf\u30a4\u30d7\uff34", 19], ["\u2020\u8056\u9a0e\u58eb \u7d05\u685c\u2020", 20], ["\u96c0\u677e\u6731\u53f8", 21], ["\u9e92\u30f6\u5cf6\u5b97\u9e9f", 22], ["\u6625\u6b4c\u30ca\u30ca", 23], ["\u732b\u4f7f\u30a2\u30eb", 24], ["\u732b\u4f7f\u30d3\u30a3", 25], ["\u4e2d\u56fd\u3046\u3055\u304e", 26], ["\u6817\u7530\u307e\u308d\u3093", 27], ["\u3042\u3044\u3048\u308b\u305f\u3093", 28], ["\u6e80\u5225\u82b1\u4e38", 29], ["\u7434\u8a60\u30cb\u30a2", 30]]
"""
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
            pass
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
                "default": "https://meowsky49887-voicevox-engine.hf.space",
            },
            {
                "key": "voice",
                "title": "Voice",
                "description": "Voice to use",
                "type": "combo",
                "values": self.voices,
                "refresh": lambda x : self.update_voices,
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

        try:
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
        except Exception as e:
            print("Endpoint does not support speakers")
            voices = json.loads(VOICES)
            ret = tuple()
            for voice in voices:
                ret += ((voice[0], voice[1]),)
            self.voices = ret
        self.set_setting("voices", self.voices)
        self.settings_update()
        return self.voices

    def set_setting(self, setting, value, default=None):
        super().set_setting(setting, value)
        if setting == "endpoint":
            self.set_setting("voices", tuple())
            threading.Thread(target=self.get_voices).start()

