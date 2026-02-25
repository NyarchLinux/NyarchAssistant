from abc import abstractmethod
from typing import Any 
from gi.repository import Gtk
import os 
import queue
import subprocess
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
        
        if tts.streaming_enabled():
            self._speak_with_tts_streaming(chunks, tts, translator, frame_rate)
        else:
            self._speak_with_tts_file_based(chunks, tts, translator, frame_rate)

    def _speak_with_tts_streaming(self, chunks: list, tts: TTSHandler, translator: TranslatorHandler, frame_rate: int = 10):
        """Speak using streaming TTS with lipsync support.

        Processes *chunks* sequentially.  For each chunk the expression/motion is
        applied first, then the text is spoken via ``speak_stream`` which subclasses
        may override to provide real-time lipsync.  Falls back to the base
        ``speak_stream`` implementation (temp-file → ``speak()``) when the subclass
        does not provide a real-time override.
        """
        self.lock.acquire()
        try:
            for chunk in chunks:
                if self.stop_request:
                    self.stop_request = False
                    break

                # Apply expression / motion immediately (before audio starts)
                if chunk["expression"] is not None:
                    if chunk["expression"] in self.get_expressions():
                        self.set_expression(chunk["expression"])
                    elif chunk["expression"] in self.get_motions():
                        self.do_motion(chunk["expression"])

                if not chunk["text"] or not chunk["text"].strip():
                    continue

                text = chunk["text"]
                if translator is not None:
                    text = translator.translate(text)

                self.speak_stream(
                    tts.get_audio_stream(text),
                    tts.get_stream_format_args(),
                    tts,
                    frame_rate,
                )
        finally:
            self.lock.release()

    def _speak_with_tts_file_based(self, chunks: list, tts: TTSHandler, translator: TranslatorHandler, frame_rate: int):
        """Speak using file-based TTS (existing approach)"""
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

    def speak(self, path: str, tts: TTSHandler, frame_rate: int):
        """Play a pre-saved audio file with lipsync.

        Uses pydub + LivePNG.calculate_amplitudes to compute per-frame mouth
        aperture from the full waveform, then plays audio and animation in
        parallel threads.

        Subclasses that require different behaviour (e.g. LivePNGHandler) should
        override this method.
        """
        from pydub import AudioSegment
        from livepng import LivePNG
        from time import sleep

        tts.stop()
        audio = AudioSegment.from_file(path)
        sample_rate = audio.frame_rate
        audio_data = audio.get_array_of_samples()
        amplitudes = LivePNG.calculate_amplitudes(sample_rate, audio_data, frame_rate=frame_rate)
        t1 = threading.Thread(target=self._start_animation, args=(amplitudes, frame_rate))
        t2 = threading.Thread(target=tts.playsound, args=(path,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def _start_animation(self, amplitudes: list, frame_rate: int = 10):
        """Drive mouth animation from a pre-computed amplitude list."""
        from time import sleep
        if not amplitudes:
            return
        max_amplitude = max(amplitudes)
        if max_amplitude == 0:
            return
        for amplitude in amplitudes:
            if self.stop_request:
                self.set_mouth(0)
                return
            self.set_mouth(amplitude / max_amplitude)
            sleep(1 / frame_rate)

    def set_mouth(self, value: float):
        """Set the mouth openness to *value* in [0.0, 1.0].

        No-op in the base class.  Subclasses with a mouth control (Live2D, VRM)
        should override this to apply the value to their renderer.
        """
        pass

    def speak_stream(self, audio_gen, format_args: list, tts: TTSHandler, frame_rate: int):
        """Play streaming audio with real-time lipsync.

        Tees the incoming byte stream to:
        1. ffmpeg → ffplay for immediate audio playback.
        2. A separate ffmpeg decoder → per-frame RMS amplitude → ``set_mouth()``.

        The decoder runs ahead of real-time so the amplitude queue fills quickly;
        the animation thread drains it at exactly *frame_rate* fps.

        Subclasses that cannot use ``set_mouth()`` (e.g. LivePNGHandler, which
        needs a complete file) should override this method.
        """
        import numpy as np
        from subprocess import Popen
        from time import sleep

        SAMPLE_RATE = 22050
        samples_per_frame = max(1, SAMPLE_RATE // frame_rate)
        bytes_per_frame = samples_per_frame * 2  # s16le mono

        stop_event = threading.Event()
        play_queue: queue.Queue = queue.Queue()
        analyze_queue: queue.Queue = queue.Queue()
        amplitude_queue: queue.Queue = queue.Queue()

        fmt_args = format_args or []

        # --- producer: tee the byte stream to both pipelines ---
        def producer():
            try:
                for chunk in audio_gen:
                    if stop_event.is_set():
                        break
                    play_queue.put(chunk)
                    analyze_queue.put(chunk)
            except Exception as e:
                print(f"speak_stream producer error: {e}")
            finally:
                play_queue.put(None)
                analyze_queue.put(None)

        # --- playback: bytes → ffmpeg (decode/resample) → ffplay ---
        def playback():
            tts.stop()
            tts._play_lock.acquire()
            tts.on_start()
            ffmpeg_play = None
            ffplay = None
            try:
                ffmpeg_play = Popen(
                    ["ffmpeg"] + fmt_args + ["-i", "-", "-f", "wav", "-"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                ffplay = Popen(
                    ["ffplay", "-nodisp", "-autoexit", "-hide_banner", "-i", "pipe:0"],
                    stdin=ffmpeg_play.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                tts.play_process = ffplay
                ffmpeg_play.stdout.close()
                while True:
                    try:
                        chunk = play_queue.get(timeout=0.5)
                    except queue.Empty:
                        if stop_event.is_set():
                            break
                        continue
                    if chunk is None or stop_event.is_set():
                        break
                    try:
                        ffmpeg_play.stdin.write(chunk)
                    except BrokenPipeError:
                        break
                try:
                    ffmpeg_play.stdin.close()
                except Exception:
                    pass
                if stop_event.is_set():
                    ffplay.terminate()
                    ffmpeg_play.terminate()
                else:
                    ffplay.wait()
                    ffmpeg_play.wait()
            except Exception as e:
                print(f"speak_stream playback error: {e}")
            finally:
                for proc in (ffplay, ffmpeg_play):
                    if proc is not None:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                tts.play_process = None
                tts.on_stop()
                tts._play_lock.release()

        # --- decoder: ffmpeg raw PCM → per-frame RMS amplitudes ---
        def decoder():
            ffmpeg = None
            try:
                ffmpeg = Popen(
                    ["ffmpeg"] + fmt_args
                    + ["-i", "pipe:0", "-f", "s16le",
                       "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )

                def feeder():
                    while True:
                        try:
                            chunk = analyze_queue.get(timeout=0.5)
                        except queue.Empty:
                            if stop_event.is_set():
                                break
                            continue
                        if chunk is None or stop_event.is_set():
                            break
                        try:
                            ffmpeg.stdin.write(chunk)
                        except BrokenPipeError:
                            break
                    try:
                        ffmpeg.stdin.close()
                    except Exception:
                        pass

                feeder_t = threading.Thread(target=feeder, daemon=True)
                feeder_t.start()

                buf = b""
                while True:
                    data = ffmpeg.stdout.read(bytes_per_frame * 4)
                    if not data:
                        break
                    buf += data
                    while len(buf) >= bytes_per_frame:
                        frame_data = buf[:bytes_per_frame]
                        buf = buf[bytes_per_frame:]
                        samples = np.frombuffer(frame_data, dtype=np.int16).astype(np.float32)
                        rms = float(np.sqrt(np.mean(samples ** 2)))
                        amplitude_queue.put(rms)

                feeder_t.join()
                if stop_event.is_set():
                    ffmpeg.terminate()
                else:
                    ffmpeg.wait()
            except Exception as e:
                print(f"speak_stream decoder error: {e}")
            finally:
                if ffmpeg is not None:
                    try:
                        ffmpeg.terminate()
                    except Exception:
                        pass
                amplitude_queue.put(None)

        # --- animation: drive mouth at frame_rate cadence ---
        def animate():
            running_max = 1.0
            while True:
                if self.stop_request:
                    self.set_mouth(0)
                    stop_event.set()
                    return
                try:
                    amplitude = amplitude_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if amplitude is None:
                    self.set_mouth(0)
                    return
                if amplitude > running_max:
                    running_max = amplitude
                self.set_mouth(amplitude / running_max)
                sleep(1.0 / frame_rate)

        t_prod = threading.Thread(target=producer, daemon=True)
        t_play = threading.Thread(target=playback)
        t_dec = threading.Thread(target=decoder, daemon=True)
        t_anim = threading.Thread(target=animate, daemon=True)

        t_prod.start()
        t_play.start()
        t_dec.start()
        t_anim.start()

        t_play.join()
        t_anim.join()
        stop_event.set()

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


