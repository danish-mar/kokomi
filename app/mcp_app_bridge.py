import os
import json
import sys
import asyncio

# 1. Prevent name shadowing: Remove script's directory (app/) from sys.path
# so that "import mcp" resolves to the official package instead of app/mcp.py
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir in sys.path:
    sys.path.remove(current_dir)

# 2. Add project root to sys.path so we can import app modules if needed
project_root = os.path.abspath(os.path.join(current_dir, ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio

# Initialize the server
server = Server("kokomi-apps-bridge")

# Root apps directory
APPS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "apps"))

def load_apps():
    apps = {}
    if not os.path.exists(APPS_DIR):
        return apps
    for item in os.listdir(APPS_DIR):
        item_path = os.path.join(APPS_DIR, item)
        if os.path.isdir(item_path):
            manifest_path = os.path.join(item_path, "manifest.json")
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r") as f:
                        manifest = json.load(f)
                    apps[item] = manifest
                except Exception as e:
                    sys.stderr.write(f"Error loading manifest for {item}: {e}\n")
    return apps

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available apps as tools."""
    tools = []
    apps = load_apps()
    for app_id, manifest in apps.items():
        if not manifest.get("enabled", True):
            continue
        
        schema = manifest.get("inputSchema")
        if not schema or not isinstance(schema, dict):
            schema = {
                "type": "object",
                "properties": {},  # Accept arbitrary input objects
                "additionalProperties": True
            }
            
        tools.append(
            types.Tool(
                name=app_id,
                description=manifest.get("description", f"Kokomi App: {app_id}"),
                inputSchema=schema
            )
        )
    return tools

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    """Execute the app and return stdout."""
    apps = load_apps()
    if name not in apps:
        raise ValueError(f"App {name} not found")
        
    manifest = apps[name]
    entrypoint = manifest.get("entrypoint", "main.py")
    app_dir = os.path.join(APPS_DIR, name)
    script_path = os.path.join(app_dir, entrypoint)
    
    if not os.path.exists(script_path):
        return [types.TextContent(type="text", text=f"Error: Entrypoint {entrypoint} not found for {name}")]
    
    args_str = json.dumps(arguments or {})
    
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", script_path, args_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        
        stdout_str = stdout.decode().strip()
        stderr_str = stderr.decode().strip()
        
        if proc.returncode != 0:
            return [
                types.TextContent(
                    type="text", 
                    text=f"App execution failed with exit code {proc.returncode}.\nStderr: {stderr_str}\nStdout: {stdout_str}"
                )
            ]
            
        return [types.TextContent(type="text", text=stdout_str)]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error executing app {name}: {str(e)}")]

async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="kokomi-apps-bridge",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
