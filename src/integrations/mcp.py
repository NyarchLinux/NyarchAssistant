from ..extensions import NewelleExtension
from ..tools import Tool, ToolResult 
import threading 
import json 
import os
import time
from gi.repository import GLib
from ..utility.system import is_flatpak, can_escape_sandbox


class _CachedTool:
    """Lightweight stand-in for an MCP SDK tool object loaded from the cache."""

    __slots__ = ("name", "description", "inputSchema")

    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class MCPIntegration(NewelleExtension):
    id = "mcp"
    name = "MCP"

    def __init__(self, pip_path, extension_path, settings):
        super().__init__(pip_path, extension_path, settings)
        self.mcp_servers = json.loads(self.settings.get_string("mcp-servers"))
        self.tools = []
        self.tools_dict = {}  # Maps tool_name -> server_info dict
        self.stdio_sessions = {}  # Keep stdio sessions alive
        self._cache_path = os.path.join(extension_path, "mcp_tool_cache.json")

        if self._load_from_cache():
            # Cache hit -- refresh in background so next startup stays fresh
            threading.Thread(target=self._background_refresh, daemon=True).start()
        else:
            self.update_tools()

    def _get_config_dir(self):
        """Return the Newelle config directory (where OAuth creds are stored)."""
        base = GLib.get_user_config_dir()
        return base if is_flatpak() else os.path.join(base, "Newelle")

    def _get_server_info(self, server):
        """Extract server info from both old (string) and new (dict) formats"""
        if isinstance(server, str):
            return {
                "type": "http",
                "url": server,
                "title": None,
                "bearer_token": None,
                "client_id": None,
                "custom_headers": None,
                "oauth_mode": False,
                "command": None,
                "args": None,
                "env": None
            }
        return {
            "type": server.get("type", "http"),
            "url": server.get("url", ""),
            "title": server.get("title"),
            "bearer_token": server.get("bearer_token"),
            "client_id": server.get("client_id"),
            "custom_headers": server.get("custom_headers"),
            "oauth_mode": server.get("oauth_mode", False),
            "command": server.get("command"),
            "args": server.get("args"),
            "env": server.get("env"),
            "catalog_id": server.get("catalog_id")
        }

    def _get_mcp_url_for_request(self, server_info):
        """Return the URL to use for MCP requests. For OAuth servers, use canonical URL (no ?login)."""
        if not server_info:
            return ""
        url = server_info.get("url", "")
        if server_info.get("oauth_mode") and url:
            from .mcp_oauth import _canonical_mcp_url
            return _canonical_mcp_url(url)
        return url

    def _build_headers(self, bearer_token=None, custom_headers=None, server_info=None):
        """Build headers dict combining bearer token and custom headers.
        When server_info has oauth_mode=True, resolve token from OAuth credentials store.
        """
        headers = {}
        token = bearer_token
        if server_info and server_info.get("oauth_mode"):
            from .mcp_oauth import get_valid_token
            url = server_info.get("url", "")
            if url:
                token = get_valid_token(url, self._get_config_dir())
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if custom_headers and isinstance(custom_headers, dict):
            headers.update(custom_headers)
        return headers

    def _get_server_identifier(self, server_info):
        """Get a unique identifier for a server"""
        if server_info.get("type") == "stdio":
            return f"stdio:{server_info.get('command')}:{':'.join(server_info.get('args', []))}"
        return server_info.get("url", "")

    # --- Tool metadata cache ---

    def _load_from_cache(self) -> bool:
        """Populate self.tools and self.tools_dict from the on-disk cache.

        Returns True if at least one server's tools were restored.
        """
        if not os.path.exists(self._cache_path):
            return False
        try:
            with open(self._cache_path, "r") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False

        loaded_any = False
        for server in self.mcp_servers:
            server_info = self._get_server_info(server)
            identifier = self._get_server_identifier(server_info)
            entry = cache.get(identifier)
            if not entry or "tools" not in entry:
                continue
            for td in entry["tools"]:
                stub = _CachedTool(td["name"], td.get("description", ""), td.get("inputSchema", {}))
                self.tools.append(stub)
                self.tools_dict[stub.name] = server_info
            loaded_any = True

        return loaded_any

    def _save_cache(self):
        """Persist current tool metadata so future startups can skip connections."""
        cache: dict = {}
        # Group tools by server identifier
        for tool in self.tools:
            server_info = self.tools_dict.get(tool.name, {})
            identifier = self._get_server_identifier(server_info)
            if identifier not in cache:
                cache[identifier] = {"tools": [], "cached_at": time.time()}
            schema = tool.inputSchema if hasattr(tool, "inputSchema") else {}
            cache[identifier]["tools"].append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": schema,
            })
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with open(self._cache_path, "w") as f:
                json.dump(cache, f)
        except OSError as e:
            print(f"MCP cache write error: {e}")

    def _background_refresh(self):
        """Re-fetch tools from all servers and update the cache."""
        old_tools = self.tools
        old_dict = self.tools_dict
        self.tools = []
        self.tools_dict = {}
        self.async_get_tools()
        if not self.tools:
            # Restore from previous state if refresh fails entirely
            self.tools = old_tools
            self.tools_dict = old_dict

    def add_mcp_server(self, url=None, title=None, bearer_token=None, client_id=None, custom_headers=None,
                       server_type="http", command=None, args=None, env=None, oauth_mode=False):
        prepared = self.prepare_mcp_server(
            url=url,
            title=title,
            bearer_token=bearer_token,
            client_id=client_id,
            custom_headers=custom_headers,
            server_type=server_type,
            command=command,
            args=args,
            env=env,
            oauth_mode=oauth_mode,
        )
        if prepared is None:
            return False
        return self.commit_mcp_server(*prepared)

    def prepare_mcp_server(self, url=None, title=None, bearer_token=None, client_id=None,
                           custom_headers=None, server_type="http", command=None, args=None,
                           env=None, oauth_mode=False):
        """Connect to a server and return its config and tools without mutating state."""
        if server_type == "stdio":
            if not command:
                return None
            server_info = {
                "type": "stdio",
                "title": title,
                "command": command,
                "args": args or [],
                "env": env
            }
            tools = self.sync_get_tools_stdio(command, args or [], env)
        else:
            if not url:
                return None
            server_info = {
                "type": "http",
                "url": url,
                "title": title,
                "bearer_token": bearer_token,
                "client_id": client_id,
                "custom_headers": custom_headers,
                "oauth_mode": oauth_mode
            }
            tools = self.sync_get_tools(url, server_info=server_info, client_id=client_id)
        return server_info, tools

    def commit_mcp_server(self, server_info, tools):
        """Add a successfully probed server to the live registry."""
        self.tools.extend(tools)
        for tool in tools:
            self.tools_dict[tool.name] = server_info
        self.mcp_servers.append(server_info)
        self.ui_controller.require_tool_update()
        self._save_cache()
        return True

    def remove_mcp_server(self, identifier):
        """Remove server by URL (http) or command identifier (stdio)"""
        server_to_remove = None
        for server in self.mcp_servers:
            if isinstance(server, str):
                server_url = server
            else:
                server_info = self._get_server_info(server)
                server_url = self._get_server_identifier(server_info)
            if server_url == identifier:
                server_to_remove = server
                if isinstance(server, dict) and server.get("oauth_mode"):
                    from .mcp_oauth import clear_oauth_credentials
                    clear_oauth_credentials(server.get("url", ""), self._get_config_dir())
                break
        if server_to_remove:
            self.mcp_servers.remove(server_to_remove)
        self.tools = []
        self.tools_dict = {}
        self.update_tools()
        self._save_cache()
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return True

    def update_tools(self):
        t = threading.Thread(target=self.async_get_tools)
        t.start()

    def async_get_tools(self) -> list:
        for server in self.mcp_servers:
            server_info = self._get_server_info(server)
            identifier = self._get_server_identifier(server_info)
            print(f"Loading tools from: {identifier}")
            try:
                if server_info.get("type") == "stdio":
                    tools = self.sync_get_tools_stdio(
                        server_info["command"], 
                        server_info.get("args") or [], 
                        server_info.get("env")
                    )
                else:
                    tools = self.sync_get_tools(
                        server_info["url"],
                        server_info=server_info,
                        client_id=server_info.get("client_id")
                    )
                print(tools)
                self.tools.extend(tools)
                for tool in tools:
                    self.tools_dict[tool.name] = server_info
            except Exception as e:
                print(f"Error fetching tools from {identifier}: {e}")
        self._save_cache()
        if hasattr(self, "ui_controller"):
            self.ui_controller.require_tool_update()
        return self.tools

    def execute_tool(self, name) -> str:
        return lambda **arguments : self.execute_tool_name(name, **arguments)

    @staticmethod
    def _image_context_message(data, mime_type) -> str | None:
        if not data:
            return None
        data = str(data)
        if data.startswith("data:image/"):
            image = data
        else:
            mime_type = str(mime_type or "image/png")
            image = f"data:{mime_type};base64,{data}"
        return f"```image\n{image}\n```"

    @classmethod
    def _format_tool_result(cls, value) -> tuple[str, list[str]]:
        """Split an MCP result into textual output and image context messages."""
        content = getattr(value, "content", None)
        if content is None:
            return str(value), []

        output_parts = []
        context_messages = []
        for item in content:
            content_type = getattr(item, "type", None)
            if content_type == "text":
                output_parts.append(str(getattr(item, "text", "")))
            elif content_type == "image":
                message = cls._image_context_message(
                    getattr(item, "data", None),
                    getattr(item, "mimeType", None),
                )
                if message is not None:
                    context_messages.append(message)
            elif content_type == "resource":
                resource = getattr(item, "resource", None)
                mime_type = getattr(resource, "mimeType", None)
                blob = getattr(resource, "blob", None)
                if mime_type and str(mime_type).startswith("image/") and blob:
                    message = cls._image_context_message(blob, mime_type)
                    if message is not None:
                        context_messages.append(message)
                elif getattr(resource, "text", None) is not None:
                    output_parts.append(str(resource.text))
                else:
                    output_parts.append(str(item))
            elif content_type == "resource_link":
                mime_type = getattr(item, "mimeType", None)
                uri = getattr(item, "uri", None)
                if mime_type and str(mime_type).startswith("image/") and uri:
                    context_messages.append(f"```image\n{uri}\n```")
                else:
                    output_parts.append(str(uri or item))
            else:
                output_parts.append(str(item))

        structured_content = getattr(value, "structuredContent", None)
        if structured_content is not None:
            output_parts.append(json.dumps(structured_content, ensure_ascii=False))

        output = "\n\n".join(part for part in output_parts if part)
        if not output:
            if context_messages:
                output = "Image content returned by MCP server."
            else:
                output = "Tool executed successfully."
        return output, context_messages

    def execute_tool_name(self, tool_name: str, **arguments) -> str:
        result = ToolResult()
        def get_answer():
            try:
                server_info = self.tools_dict.get(tool_name, {})
                if not server_info:
                    result.set_output("Error: Tool server not found")
                    return

                if server_info.get("type") == "stdio":
                    command = server_info.get("command")
                    args = server_info.get("args") or []
                    env = server_info.get("env")
                    if not command:
                        result.set_output("Error: Stdio server command not found")
                        return
                    value = self.sync_call_tool_stdio(command, args, env, tool_name, arguments)
                else:
                    url = server_info.get("url")
                    if not url:
                        result.set_output("Error: HTTP server URL not found")
                        return
                    value = self.sync_call_tool(
                        url, tool_name, arguments,
                        server_info=server_info,
                        client_id=server_info.get("client_id")
                    )
                output, context_messages = self._format_tool_result(value)
                result.set_context_messages(context_messages)
                result.set_output(output)
            except Exception as error:
                result.set_output(f"Error: {error}")
        t = threading.Thread(target=get_answer)
        t.start()
        return result

    def _tool_search(self, tool_name: str) -> ToolResult:
        """Meta-tool: return the full parameter schema for a given tool."""
        result = ToolResult()
        if hasattr(self, "ui_controller") and self.ui_controller is not None:
            controller = self.ui_controller.window.controller
            # Record that this tool's schema has been discovered so subsequent
            # turns emit its full parameters — required for native tool calling,
            # which cannot act on the compact (parameter-less) definition.
            if hasattr(controller, "expanded_tools"):
                controller.expanded_tools.add(tool_name)
            result.set_output(controller.tools.get_tool_schema(tool_name))
        else:
            result.set_output(json.dumps({"error": "Controller not available"}))
        return result

    def get_tools(self) -> list:
        tools = []
        for tool in self.tools:
            server_info = self.tools_dict.get(tool.name, {})
            tools_group = server_info.get("title") or server_info.get("url", "MCP")
            tools.append(Tool(
                tool.name, tool.description, self.execute_tool(tool.name),
                tool.inputSchema, tools_group=tools_group, default_lazy_load=True,
            ))
        if tools:
            tool_search = Tool(
                "tool_search",
                "Get the full parameter schema for a tool. Call this FIRST, before invoking any tool listed without parameters (marked 'compact').",
                lambda tool_name: self._tool_search(tool_name),
                schema={
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "The name of the tool to look up",
                        }
                    },
                    "required": ["tool_name"],
                },
                tools_group="Agent",
                default_lazy_load=False,
                icon_name="system-search-symbolic",
            )
            tools.append(tool_search)
        return tools

    def get_answer(self, codeblock: str, lang: str) -> str | None:
        print(codeblock) 
        js = codeblock
        print(js)
        call = json.loads(js)
        print(call)
        if "tool" not in call:
            return "Missing tool name"
        tool_name = call["tool"]
        args = call["arguments"]
        result = self.sync_call_tool(tool_name, args)
        return result

    def _stdio_server_params(self, command, args, env):
        """Build StdioServerParameters for a stdio server.

        Inside a Flatpak sandbox the configured command (npx, node, python, …)
        is not available, so run it on the host via ``flatpak-spawn --host``.

        The MCP SDK spawns the server with a minimal allowlist environment
        (HOME, PATH, USER, …) that drops ``XDG_RUNTIME_DIR`` and
        ``DBUS_SESSION_BUS_ADDRESS``; flatpak-spawn needs those to reach the
        session helper, so the full current environment is passed through here.
        Each user-supplied variable is also forwarded explicitly with ``--env``
        so it reaches the host command.
        """
        from mcp.client.stdio import StdioServerParameters

        args = args or []
        if is_flatpak() and can_escape_sandbox():
            spawn_args = ["--host"]
            if env and isinstance(env, dict):
                for key, value in env.items():
                    spawn_args.append(f"--env={key}={value}")
            spawn_args.append(command)
            spawn_args.extend(args)
            return StdioServerParameters(command="flatpak-spawn", args=spawn_args, env=dict(os.environ))
        process_env = dict(os.environ)
        if env and isinstance(env, dict):
            process_env.update(env)
        return StdioServerParameters(command=command, args=args, env=process_env)

    def sync_get_tools(self, url, headers=None, client_id=None, server_info=None):
        """Synchronous wrapper to get available tools (HTTP)"""
        import asyncio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        
        if headers is None:
            headers = {}
        resolved_headers = self._build_headers(
            server_info.get("bearer_token") if server_info else None,
            server_info.get("custom_headers") if server_info else headers,
            server_info
        )
        request_url = (self._get_mcp_url_for_request(server_info) or url) if server_info else url
        
        async def _async_get_tools():
            client_kwargs = {"url": request_url, "headers": resolved_headers}
            async with streamablehttp_client(**client_kwargs) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools.tools
        return asyncio.run(_async_get_tools())

    def sync_call_tool(self, url, tool_name, arguments, headers=None, client_id=None, server_info=None):
        """Synchronous wrapper to call a tool"""
        import asyncio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        
        if headers is None:
            headers = {}
        resolved_headers = self._build_headers(
            server_info.get("bearer_token") if server_info else None,
            server_info.get("custom_headers") if server_info else headers,
            server_info
        )
        request_url = (self._get_mcp_url_for_request(server_info) or url) if server_info else url
        
        async def _async_call_tool():
            client_kwargs = {"url": request_url, "headers": resolved_headers}
            async with streamablehttp_client(**client_kwargs) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return result
        return asyncio.run(_async_call_tool())

    def sync_get_tools_stdio(self, command, args=None, env=None):
        """Synchronous wrapper to get available tools from stdio server"""
        import asyncio
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession

        async def _async_get_tools():
            server_params = self._stdio_server_params(command, args, env)
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return tools.tools
        
        return asyncio.run(_async_get_tools())

    def sync_call_tool_stdio(self, command, args, env, tool_name, arguments):
        """Synchronous wrapper to call a tool on stdio server"""
        import asyncio
        from mcp.client.stdio import stdio_client
        from mcp import ClientSession

        async def _async_call_tool():
            server_params = self._stdio_server_params(command, args, env)
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
                    return result
        
        return asyncio.run(_async_call_tool())
