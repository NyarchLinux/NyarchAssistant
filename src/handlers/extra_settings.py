
from collections.abc import Callable


class ExtraSettings:
    @staticmethod 
    def Setting(key: str, title: str, description: str, default: object, folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = {
            "key": key,
            "title": title,
            "description": description,
            "default": default,
            "update_settings": update_settings
        }
        if website is not None:
            r["website"] = website
        if folder is not None:
            r["folder"] = folder
        if refresh is not None:
            r["refresh"] = refresh
        if refresh_icon is not None:
            r["refresh_icon"] = refresh_icon
        return r
    @staticmethod
    def EntrySetting(key:str, title: str, description: str, default: str, 
                     folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "entry"
        return r

    @staticmethod
    def ToggleSetting(key:str, title: str, description: str, default: bool, 
                      folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "toggle"
        return r

    @staticmethod 
    def NestedSetting(key:str, title: str, description: str, default: list, extra_settings: list, 
                      folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "nested"
        r["extra_settings"] = extra_settings
        return r

    @staticmethod 
    def DownloadSetting(key:str, title: str, description: str, is_installed: bool, callback: Callable, download_percentage: Callable, 
                        folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = ExtraSettings.Setting(key, title, description, is_installed, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "download"
        r["callback"] = callback
        r["download_percentage"] = download_percentage
        r["is_installed"] = is_installed
        return r

    @staticmethod
    def ButtonSetting(key:str, title: str, description: str, callback: Callable, label: str|None = None, icon: str|None = None,
                      folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = ExtraSettings.Setting(key, title, description, None, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "button"
        r["callback"] = callback
        if label is not None:
            r["label"] = label
        if icon is not None:
            r["icon"] = icon
        return r

    @staticmethod
    def ComboSetting(key: str, title: str, description: str, values: list | dict | tuple, default: str,
                     folder: str|None = None, website: str|None = None, update_settings: bool = False, refresh: Callable|None = None, refresh_icon: str|None = None) -> dict:
        r = ExtraSettings.Setting(key, title, description, default, folder, website, update_settings, refresh, refresh_icon)
        r["type"] = "combo"
        if type(values) is list:
            val = tuple()
            for v in values:
                val += ((v,v))
        elif type(values) is dict:
            val = tuple()
            for k, v in values.items():
                val += ((k, v))
        else:
            val = values

        r["values"] = values
        return r

