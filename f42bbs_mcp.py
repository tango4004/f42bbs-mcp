"""
F42BBS MCP Server — exposes F42BBS network as MCP tools
Endpoint: https://bbs.foxtrot42.org/mcp
"""
import os, json, time, requests
from flask import Flask, request, Response
from dotenv import load_dotenv

load_dotenv()

BBS_NODE_URL = os.getenv("BBS_NODE_URL", "https://bbs3.foxtrot42.org/step")
MCP_TOKEN    = os.getenv("MCP_TOKEN", "")
REQUEST_TIMEOUT = int(os.getenv("STEP_REQUEST_TIMEOUT", "10"))
PORT = int(os.getenv("MCP_PORT", "8005"))

app = Flask(__name__)

# ── charlie2.Session (per-request, stateless) ──────────────────────────────

def bbs_call(command: str) -> str:
    """Single-shot: new session → command → result"""
    # step 1: new session
    r = requests.post(BBS_NODE_URL, data=f",{command}".encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"}, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    parts = r.text.strip().split(" ", 1)
    sid = parts[0]
    # If result already in first response (e.g. help)
    if len(parts) > 1 and parts[1]:
        return parts[1]
    # step 2: not expected for init — return sid
    return sid

def bbs_run(command: str) -> str:
    """Two-step: init session, then run command"""
    # init
    r0 = requests.post(BBS_NODE_URL, data=",".encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"}, timeout=10)
    r0.raise_for_status()
    r0.encoding = "utf-8"
    sid = r0.text.strip().split()[0]
    # run
    r1 = requests.post(BBS_NODE_URL, data=f"{sid} {command}".encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"}, timeout=15)
    r1.raise_for_status()
    parts = r1.text.strip().split(" ", 1)
    return parts[1] if len(parts) > 1 else parts[0]

# ── Auth ───────────────────────────────────────────────────────────────────

def check_auth():
    if not MCP_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {MCP_TOKEN}"

# ── Tools ──────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "bbs_publish",
        "description": (
            "Publish a message to a topic on the F42BBS network.\n"
            "The message will be propagated to all federated nodes.\n"
            "Returns confirmation string."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic name (e.g. 'hello-world')"},
                "body":  {"type": "string", "description": "Message body to publish"}
            },
            "required": ["topic", "body"]
        }
    },
    {
        "name": "bbs_get",
        "description": (
            "Get the latest message from a topic on the F42BBS network.\n"
            "Returns the message body, or 'no messages in topic <name>'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic name to read from"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "bbs_request",
        "description": (
            "Send a REQUEST to the F42BBS network and wait for a DIGEST response.\n"
            "Nodes will auto-respond with a digest of the latest content on that topic.\n"
            f"Waits up to {REQUEST_TIMEOUT} seconds. Returns digest body or timeout message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to request digest for"},
                "query": {"type": "string", "description": "Query or question for the network"}
            },
            "required": ["topic", "query"]
        }
    }
]

def handle_tool(name: str, args: dict) -> str:
    try:
        if name == "bbs_publish":
            return bbs_run(f"publish topic={args['topic']} body={args['body']}")

        elif name == "bbs_get":
            return bbs_run(f"get topic={args['topic']}")

        elif name == "bbs_request":
            return bbs_run(f"request topic={args['topic']} query={args.get('query','latest')}")

        else:
            return f"error: unknown tool '{name}'"

    except Exception as e:
        return f"error: {e}"

# ── MCP HTTP handler ───────────────────────────────────────────────────────

def mcp_ok(id_, result):
    return Response(json.dumps({"jsonrpc":"2.0","id":id_,"result":result}, ensure_ascii=False),
        mimetype="application/json")

def mcp_err(id_, code, msg):
    return Response(json.dumps({"jsonrpc":"2.0","id":id_,
        "error":{"code":code,"message":msg}}, ensure_ascii=False), mimetype="application/json")

@app.route("/mcp", methods=["GET"])
def health():
    return Response("f42bbs-mcp ok\n", mimetype="text/plain")

@app.route("/mcp", methods=["POST"])
def mcp():
    if not check_auth():
        return Response(json.dumps({"error":"unauthorized"}, ensure_ascii=False),
            status=401, mimetype="application/json")

    try:
        body = request.get_json()
    except Exception:
        return mcp_err(None, -32700, "parse error")

    if not body:
        return Response("", status=204)

    method = body.get("method", "")
    id_    = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return mcp_ok(id_, {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "f42bbs-mcp", "version": "0.1.0"}
        })

    if method in ("notifications/initialized",):
        return Response("", status=204)

    if method == "tools/list":
        return mcp_ok(id_, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        result = handle_tool(name, args)
        return mcp_ok(id_, {"content": [{"type": "text", "text": result}]})

    return mcp_err(id_, -32601, f"unknown method: {method}")


if __name__ == "__main__":
    print(f"[f42bbs-mcp] starting on port {PORT}, node={BBS_NODE_URL}", flush=True)
    app.run(host="0.0.0.0", port=PORT, debug=False)
