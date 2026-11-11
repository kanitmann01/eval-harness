import json, subprocess, threading, queue, sys, time, os
q = queue.Queue()
p = subprocess.Popen(
    ["npx.cmd", "-y", "@next-ai-drawio/mcp-server@latest"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1, encoding="utf-8",
    env={**os.environ, "BROWSER": "none", "DRAWIO_ALLOW_EMBED": "true", "DRAWIO_LANG": "en"},
)
def reader():
    for line in p.stdout:
        q.put(line)
threading.Thread(target=reader, daemon=True).start()
def send(obj):
    p.stdin.write(json.dumps(obj) + "\n"); p.stdin.flush()
def recv(t=180):
    line = q.get(timeout=t)
    return json.loads(line)
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"dsh-blog","version":"1.0"}}})
print("INIT", json.dumps(recv(), indent=2)[:1000])
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
while True:
    m = recv()
    if m.get("id") == 2:
        break
for tool in m["result"]["tools"]:
    print("\nTOOL", tool["name"])
    print(json.dumps(tool.get("inputSchema"), indent=2)[:2200])
p.terminate()
