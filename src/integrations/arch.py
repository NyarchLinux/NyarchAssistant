from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult
import threading
import json
import urllib.request
import urllib.parse
import urllib.error
import re

ARCH_PROMPT = """{COND:
[search_arch_wiki] When a user asks for information about Arch Linux, such as installation guides, configuration steps, or troubleshooting, you should prioritize searching the Arch Wiki using the `search_arch_wiki` tool. 
}
{COND:
[get_wiki_page] Once you find a relevant page, use `get_wiki_page` to fetch the detailed information. 
}
{COND:
[get_official_package_info or search_aur] For questions about specific software packages available in the Arch repositories or the AUR, use `get_official_package_info` or `search_aur` to provide accurate package details.
}
"""


class ArchLinuxExtension(NewelleExtension):
    id = "arch_linux"
    name = "Arch Linux Tools"
        
    WIKI_API_URL = "https://wiki.archlinux.org/api.php"
    DEFAULT_TIMEOUT = 15

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)

    def _fetch_data(self, url, params=None):
        """Helper to fetch data from API using urllib (standard library only)"""
        try:
            if params:
                url = f"{url}?{urllib.parse.urlencode(params)}"
            
            request = urllib.request.Request(url)
            request.add_header('User-Agent', 'Newelle/1.0')
            
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read().decode('utf-8')
                return json.loads(data)
        except urllib.error.HTTPError as e:
            return f"Error fetching data: HTTP {e.code} - {e.reason}"
        except urllib.error.URLError as e:
            return f"Error fetching data: {str(e)}"
        except json.JSONDecodeError as e:
            return f"Error parsing JSON: {str(e)}"
        except Exception as e:
            return f"Error fetching data: {str(e)}"

    def search_arch_wiki(self, query: str):
        """Search Arch Wiki with ranked results"""
        result = ToolResult()
        
        def execute():
            url = "https://wiki.archlinux.org/api.php"
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 10,
                "srqiprofile": "engine_autoselect"
            }
            data = self._fetch_data(url, params)
            if isinstance(data, str):
                result.set_output(data)
                return

            search_results = data.get("query", {}).get("search", [])
            formatted_results = []
            for res in search_results:
                title = res.get("title")
                snippet = res.get("snippet", "").replace('<span class="searchmatch">', '').replace('</span>', '')
                url_wiki = f"https://wiki.archlinux.org/title/{title.replace(' ', '_')}"
                formatted_results.append(f"### [{title}]({url_wiki})\n{snippet}...")
            
            if not formatted_results:
                result.set_output("No results found on Arch Wiki.")
            else:
                result.set_output("\n\n".join(formatted_results))
        
        thread = threading.Thread(target=execute)
        thread.start()
        return result

    def search_aur(self, query: str, sort_by: str = "relevance"):
        """Search AUR (relevance/votes/popularity/modified)"""
        result = ToolResult()
        
        def execute():
            url = "https://aur.archlinux.org/rpc/"
            params = {
                "v": 5,
                "type": "search",
                "arg": query
            }
            data = self._fetch_data(url, params)
            if isinstance(data, str):
                result.set_output(data)
                return

            results = data.get("results", [])
            
            # AUR API doesn't support server-side sorting for all these fields in one type
            if sort_by == "votes":
                results.sort(key=lambda x: x.get("NumVotes", 0), reverse=True)
            elif sort_by == "popularity":
                results.sort(key=lambda x: x.get("Popularity", 0), reverse=True)
            elif sort_by == "modified":
                results.sort(key=lambda x: x.get("LastModified", 0), reverse=True)
            # relevance is default from API
            
            formatted_results = []
            for pkg in results[:10]:
                name = pkg.get("Name")
                version = pkg.get("Version")
                description = pkg.get("Description")
                votes = pkg.get("NumVotes")
                popularity = pkg.get("Popularity")
                formatted_results.append(f"**{name}** {version} ({votes} votes, {popularity} popularity)\n{description}")
            
            if not formatted_results:
                result.set_output("No results found in AUR.")
            else:
                result.set_output("\n\n".join(formatted_results))
        
        thread = threading.Thread(target=execute)
        thread.start()
        return result

    def get_official_package_info(self, package_name: str):
        """Get official package details (hybrid local/remote)"""
        result = ToolResult()
        
        def execute():
            url = f"https://archlinux.org/packages/search/json/?name={package_name}"
            data = self._fetch_data(url)
            if isinstance(data, str):
                result.set_output(data)
                return

            results = data.get("results", [])
            if not results:
                result.set_output(f"Package '{package_name}' not found in official repositories.")
                return
            
            pkg = results[0]
            info = [
                f"# {pkg.get('pkgname')} {pkg.get('pkgver')}-{pkg.get('pkgrel')}",
                f"**Description:** {pkg.get('pkgdesc')}",
                f"**Repository:** {pkg.get('repo')}",
                f"**Architecture:** {pkg.get('arch')}",
                f"**URL:** {pkg.get('url')}",
                f"**Licenses:** {', '.join(pkg.get('licenses', []))}",
                f"**Maintainers:** {', '.join(pkg.get('maintainers', []))}",
                f"**Package Size:** {pkg.get('compressed_size', 0) / 1024 / 1024:.2f} MB",
                f"**Installed Size:** {pkg.get('installed_size', 0) / 1024 / 1024:.2f} MB",
            ]
            result.set_output("\n".join(info))
        
        thread = threading.Thread(target=execute)
        thread.start()
        return result

    def _fetch_wiki_page_via_api(self, title: str):
        """
        Fetch page content via MediaWiki API.
        
        Args:
            title: Page title
        
        Returns:
            HTML content or None if failed
        """
        params = {
            "action": "parse",
            "page": title,
            "format": "json",
            "prop": "text",
            "disableeditsection": "1",
            "disabletoc": "1"
        }
        
        try:
            data = self._fetch_data(self.WIKI_API_URL, params)
            
            # Check if _fetch_data returned an error string
            if isinstance(data, str):
                return None
            
            # Check for errors in response
            if "error" in data:
                return None
            
            # Extract HTML content
            if "parse" in data and "text" in data["parse"]:
                html_content = data["parse"]["text"]["*"]
                return html_content
            
            return None
            
        except Exception:
            return None

    def get_wiki_page(self, page_title: str):
        """Get information from an Arch Wiki page"""
        result = ToolResult()
        
        def execute():
            html_content = self._fetch_wiki_page_via_api(page_title)
            if html_content is None:
                result.set_output(f"Page '{page_title}' not found on Arch Wiki or could not be retrieved.")
                return

            # Try to convert HTML to Markdown if markdownify is available
            try:
                import markdownify
                content = markdownify.markdownify(html_content)
            except ImportError:
                content = html_content
            
            cleaned_content = self.clean(content)
            result.set_output(cleaned_content)
        
        thread = threading.Thread(target=execute)
        thread.start()
        return result

    def get_tools(self) -> list:
        """Return the list of tools provided by this extension"""

        return [
            Tool(
                name="search_arch_wiki",
                description="Search Arch Wiki with ranked results",
                func=self.search_arch_wiki,
                title="Search Arch Wiki",
                tools_group="Arch Linux"
            ),
            Tool(
                name="search_aur",
                description="Search AUR packages by relevance, votes, popularity, or modification date",
                func=self.search_aur,
                title="Search AUR Packages",
                tools_group="Arch Linux"
            ),
            Tool(
                name="get_official_package_info",
                description="Get detailed information about an official Arch Linux package",
                func=self.get_official_package_info,
                title="Get Official Package Info",
                tools_group="Arch Linux"
            ),
            Tool(
                name="get_wiki_page",
                description="Get information from an Arch Wiki page",
                func=self.get_wiki_page,
                title="Get Wiki Page",
                tools_group="Arch Linux"
            )
        ]

    def get_replace_codeblocks_langs(self) -> list:
        return ["arch-wiki"]

    def get_additional_prompts(self) -> list:
        return [
            {
                "key": "archwiki",
                "setting_name": "archwiki",
                "title": "Arch Wiki",
                "description": "Tell the LLM when to search on the arch wiki",
                "editable": True,
                "show_in_settings": True,
                "default": True,
                "text": ARCH_PROMPT, 
            }
        ]
    
    @staticmethod
    def clean(text):
        if not text:
            return ""
        # Remove urls
        text = re.sub(r'https?://\S+', '', text) 
        # Remove HTML
        text = re.sub(r'<[^>]*>', '', text)  
        # Remove empty lines
        text = re.sub(r'\n\s*\n', '\n', text)
        # Change title format
        text = re.sub(r'==([^=]+)==', r'\n\1\n', text)
        # Change subtitle format
        text = re.sub(r'===(.+?)===', r'\n\1\n', text)
        return text
