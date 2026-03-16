<h1 align="center">
  <img src="https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/data/icons/hicolor/scalable/apps/moe.nyarchlinux.assistant.svg" alt="nyarchassistant" width="192" height="192"/>
  <br>
  Nyarch Assistant - Your ultimate Waifu AI Assistant
</h1>
<p align="center">
    <a href="https://github.com/topics/newelle-extension">
      <picture>
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension.svg" media="(prefers-color-scheme: light)">
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension-dark.svg" media="(prefers-color-scheme: dark)">
        <img width="200" alt="Download on Flathub" src="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension.svg"/>
      </picture>
    </a>
    <a href="https://github.com/qwersyk/Newelle/wiki">
      <picture>
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki.svg" media="(prefers-color-scheme: light)">
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki-dark.svg" media="(prefers-color-scheme: dark)">
        <img width="200" alt="Wiki for Newelle" src="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki.svg"/>
      </picture>
    </a>
	<a href="https://newelle.qsk.me/discord">
      <picture>
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/discord.svg" media="(prefers-color-scheme: light)">
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/discord-dark.svg" media="(prefers-color-scheme: dark)">
        <img width="200" alt="Join our discord" src="https://raw.githubusercontent.com/qwersyk/Assets/main/discord.svg"/>
      </picture>
    </a>
    <br>
</p>

![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1b.png#gh-dark-mode-only)

https://github.com/user-attachments/assets/909edf0e-5a79-40c2-a3ef-cb5b2b18abfc

# Features
- <img src="https://nyarchlinux.moe/waifu.webp" width="20px"/> **Your waifu at your command**: Chat with your waifu using a Live2D or LivePNG model with expression and motion support
- 👤 **Harem Support**: Switch between waifus on the go
- 🎨 **Advanced Customization**: Tailor the application with a wide range of settings
- 🚀 **Flexible Model Support**: Choose from mutliple AI models and providers to fit your specific needs
- 📚 **Local models**: Download local models directly from Newelle, and run them using Llama.cpp or Ollama.
- 💻 **Terminal Command Exection**: Execute commands suggested by the AI on the fly
- 🧩 **Extensions**: Add your own functionalities and models to Newelle
- 🛠 **Tools & MCP support**: Add tools via MCP servers or extensions 
- 🗣 **Voice support**: Chat hands free with your waifu, supporting many Speech To Text and TTS models, with translation option
- 🧠 **Long Term Memory**: Remember conversations from previous chats
- 💼 **Chat with documents**: Chat with your own documents
- 🔎 **Web Search**: Provide reliable answers using Web Search
- 🌐 **Website Reading**: Ask any information about any website by writing #https://.. question
- 👤 **Profile Manager**: Create settings profiles and switch between them on the go
- 📁 **Builtin File Manager**: Manage your files with the help of AI
- 📝 **Rich Formatting**: Support for Markdown and LaTeX
- 🌳 **Chat Branching**: Create alternative versions of your chats strating from any message in one click
- ✏️ **Chat editing**: Edit or remove any message and manage your prompts easily

![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/3w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/3b.png#gh-dark-mode-only)

# Extensions

Nyarch Assistant supports extensions to enhance its functionality. You can either use [existing extensions](https://github.com/topics/newelle-extension) or create your own to add new features to the application.


![screenshot](https://raw.githubusercontent.com/qwersyk/newelle/master/screenshots/2w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/qwersyk/newelle/master/screenshots/2b.png#gh-dark-mode-only)

## Mini Window Mode

A lightweight version of Nyarch Assistant that can be triggered via keyboard shortcuts.

### Configuration

#### 1. Set Global Keyboard Shortcut
To configure the mini window launch (example using Ctrl+Space), set the following command in your system keyboard settings:

```bash
/bin/bash -c 'flatpak run --command=gsettings moe.nyarchlinux.assistant set moe.nyarchlinux.assistant startup-mode "mini" && flatpak run moe.nyarchlinux.assistant'
```

#### 2. Enable Window Centering
For GNOME desktop environment users, you may need to enable automatic window centering:

```bash
gsettings set org.gnome.mutter center-new-windows true
```

# Installation
Nyarch Assistant can be installed on **any Linux distribution** supporting Flatpak. If you are not on Arch, it is suggested to disable Smart Prompts since they might give information specific for Arch Linux.

**Normal Install**
1. Download the latest Flatpak bundle from [Github Releases](https://github.com/NyarchLinux/NyarchAssistant/releases)
2. Install it opening the file (if you have a software store supporting flatpak installed), or use `flatpak install nyarchassistant.flatpak`

**One command Install**
(Assumes to have flatpak with flathub)
```bash
cd /tmp
wget https://github.com/nyarchlinux/nyarchassistant/releases/latest/download/nyarchassistant.flatpak
flatpak install nyarchassistant.flatpak
```

![builder](https://raw.githubusercontent.com/qwersyk/Assets/main/builder.svg#gh-light-mode-only)
![builder](https://raw.githubusercontent.com/qwersyk/Assets/main/builder-dark.svg#gh-dark-mode-only)

1. Install GNOME Builder on your system.
2. Clone the nyarchassistant repository from GitHub.
3. Open the project in GNOME Builder and compile it.
4. Once compiled, you can run the program from the compiled executable.

![beta](https://raw.githubusercontent.com/qwersyk/Assets/main/beta.svg#gh-light-mode-only)
![beta](https://raw.githubusercontent.com/qwersyk/Assets/main/beta-dark.svg#gh-dark-mode-only)

1. Download the latest release from the [Github Actions](https://github.com/nyarchlinux/nyarchassistant/actions)
2. Extract the downloaded package.
3. Install a flatpak package.

# Permission

To extend functionality, you can either temporarily grant access with:
```flatpak run --talk-name=org.freedesktop.Flatpak --filesystem=home moe.nyarchlinux.assistant```
or adjust settings permanently using Flatseal:
- Open Flatseal, find "nyarchassistant," enable "All user files" and "Session Bus," and add `org.freedesktop.Flatpak` to run outside the sandbox.

> [!WARNING]
> Be cautious when enabling these options. They reduce security by exposing your data and terminal. Avoid sharing personal information, and understand that we can't guarantee the privacy of your chat data or prevent potential risks from proprietary models.
