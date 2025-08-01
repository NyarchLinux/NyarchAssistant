pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
moduledir = join_paths(pkgdatadir, 'nyarchassistant')
gnome = import('gnome')

gnome.compile_resources('nyarchassistant',
  'nyarchassistant.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')
conf = configuration_data() 
conf.set('PYTHON', python.find_installation('python3').path())
profile = get_option('profile')
version_full = meson.project_version()
if profile == 'development'
  version_full += '-' + run_command('git', 'rev-parse', '--short', 'HEAD').stdout().strip()
endif
conf.set('VERSION', version_full)
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'nyarchassistant.in',
  output: 'nyarchassistant',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'r-xr--r--'
)

nyarchassistant_sources = [
  '__init__.py',
  'main.py',
  'constants.py',
  'window.py',
  'extensions.py',
  'controller.py',
  'ui_controller.py',
  'dataset.py',
]

integrations_sources = [
  'integrations/__init__.py',
  'integrations/website_reader.py',
  'integrations/websearch.py',
]

ui_sources = [
  'ui/__init__.py',
  'ui/profile.py',
  'ui/extension.py',
  'ui/settings.py',
  'ui/presentation.py',
  'ui/thread_editing.py',
  'ui/screenrecorder.py',
  'ui/shortcuts.py',
  'ui/mini_window.py',
  'ui/explorer.py',
  'ui/stdout_monitor.py',
]

widgets_sources = [
  'ui/widgets/__init__.py',
  'ui/widgets/multiline.py',
  'ui/widgets/profilerow.py',
  'ui/widgets/barchart.py',
  'ui/widgets/comborow.py',
  'ui/widgets/copybox.py',
  'ui/widgets/file.py',
  'ui/widgets/latex.py',
  'ui/widgets/terminal_dialog.py',
  'ui/widgets/markuptextview.py',
  'ui/widgets/website.py',
  'ui/widgets/websearch.py',
  'ui/widgets/thinking.py',
  'ui/widgets/documents_reader.py',
  'ui/widgets/tipscarousel.py',
  'ui/widgets/browser.py',
  'ui/widgets/code_editor.py',
]

handler_sources = [
  'handlers/__init__.py',
  'handlers/handler.py',
  'handlers/extra_settings.py',
  'handlers/descriptors.py',
]

llm_sources = [
  'handlers/llm/__init__.py',
  'handlers/llm/llm.py',
  'handlers/llm/claude_handler.py',
  'handlers/llm/custom_handler.py',
  'handlers/llm/g4f_handler.py',
  'handlers/llm/gemini_handler.py',
  'handlers/llm/gpt3any_handler.py',
  'handlers/llm/gpt4all_handler.py',
  'handlers/llm/groq_handler.py',
  'handlers/llm/mistral_handler.py',
  'handlers/llm/newelle_handler.py',
  'handlers/llm/ollama_handler.py',
  'handlers/llm/openai_handler.py',
  'handlers/llm/openrouter_handler.py',
  'handlers/llm/deepseek_handler.py',
  'handlers/llm/nyarch_handler.py',
  ]

tts_sources = [
  'handlers/tts/__init__.py',
  'handlers/tts/tts.py',
  'handlers/tts/gtts_handler.py',
  'handlers/tts/espeak_handler.py',
  'handlers/tts/elevenlabs_handler.py',
  'handlers/tts/custom_handler.py',
  'handlers/tts/kokoro_handler.py',
  'handlers/tts/openai_tts_handler.py',
  'handlers/tts/custom_openai_tts.py',
  'handlers/tts/groq_tts_handler.py',
  'handlers/tts/vits_handler.py',
  'handlers/tts/voicevox_handler.py',
  'handlers/tts/edge_handler.py',
]

stt_sources = [  
  'handlers/stt/__init__.py',
  'handlers/stt/stt.py',
  'handlers/stt/googlesr_handler.py',
  'handlers/stt/groqsr_handler.py',
  'handlers/stt/openaisr_handler.py',
  'handlers/stt/sphinx_handler.py',
  'handlers/stt/witai_handler.py',
  'handlers/stt/whisper_handler.py',
  'handlers/stt/whispercpp_handler.py',
  'handlers/stt/custom_handler.py',
  'handlers/stt/vosk_handler.py',
]

embedding_sources = [  
  'handlers/embeddings/__init__.py',
  'handlers/embeddings/embedding.py',
  'handlers/embeddings/wordllama_handler.py',
  'handlers/embeddings/openai_handler.py',
  'handlers/embeddings/gemini_handler.py',
  'handlers/embeddings/ollama_handler.py',
]

memory_sources = [  
  'handlers/memory/__init__.py',
  'handlers/memory/memory_handler.py',
  'handlers/memory/memoripy_handler.py',
  'handlers/memory/summary_memoripy_handler.py',
  'handlers/memory/user_summary_handler.py',
]

rag_sources = [  
  'handlers/rag/__init__.py',
  'handlers/rag/rag_handler.py',
  'handlers/rag/llamaindex_handler.py',
]

websearch_sources = [  
  'handlers/websearch/__init__.py',
  'handlers/websearch/websearch.py',
  'handlers/websearch/searxng.py',
  'handlers/websearch/duckduckgo_handler.py',
  'handlers/websearch/tavily.py',
]

utility_sources = [
  'utility/__init__.py',
  'utility/media.py',
  'utility/pip.py',
  'utility/replacehelper.py',
  'utility/strings.py',
  'utility/system.py',
  'utility/util.py',
  'utility/profile_settings.py',
  'utility/audio_recorder.py',
  'utility/message_chunk.py',
  'utility/website_scraper.py',
  'utility/stdout_capture.py',
  'utility/force_sync.py',
]

avatar_sources = [
 'handlers/avatar/__init__.py',
 'handlers/avatar/avatar.py',
 'handlers/avatar/live2d_handler.py',
 'handlers/avatar/livepng_handler.py'
]

translator_sources = [
 'handlers/translator/__init__.py',
 'handlers/translator/translator.py',
 'handlers/translator/customtr_handler.py',
 'handlers/translator/googletr_handler.py',
 'handlers/translator/libretranslate_handler.py',
 'handlers/translator/ligva_handler.py',
]

smart_prompts_sources = [
 'handlers/smart_prompt/__init__.py',
 'handlers/smart_prompt/smart_prompt.py',
]

install_data(nyarchassistant_sources, install_dir: moduledir)
install_data(ui_sources, install_dir: moduledir / 'ui')
install_data(widgets_sources, install_dir: moduledir / 'ui' / 'widgets')
install_data(handler_sources, install_dir: moduledir / 'handlers')
install_data(llm_sources, install_dir: moduledir / 'handlers' / 'llm')
install_data(tts_sources, install_dir: moduledir / 'handlers' / 'tts')
install_data(stt_sources, install_dir: moduledir / 'handlers' / 'stt')
install_data(memory_sources, install_dir: moduledir / 'handlers' / 'memory')
install_data(rag_sources, install_dir: moduledir / 'handlers' / 'rag')
install_data(embedding_sources, install_dir: moduledir / 'handlers' / 'embeddings')
install_data(websearch_sources, install_dir: moduledir / 'handlers' / 'websearch')
install_data(utility_sources, install_dir: moduledir / 'utility')
install_data(integrations_sources, install_dir: moduledir / 'integrations')

install_data(translator_sources, install_dir: moduledir / 'handlers' / 'translator')
install_data(avatar_sources, install_dir: moduledir / 'handlers' / 'avatar')
install_data(smart_prompts_sources, install_dir: moduledir / 'handlers' / 'smart_prompt')
install_data(utility_sources, install_dir: moduledir / 'utility')
