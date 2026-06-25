"""
text_client.py – Command-line WebSocket JSON client for the weather-forecast agent.

Sends a `weather.request` text frame to the local `/invocations_ws` endpoint and
prints the agent's JSON response. Useful for smoke-testing the server before
configuring a full voice session.

Usage
-----
# Ask for today's weather in Tokyo
python client/text_client.py --text "今日の東京の天気は？"

# Ask without a location (triggers clarification response)
python client/text_client.py --text "今日の天気は？"

# Custom host / port / session
python client/text_client.py --text "大阪の明日の天気は？" --host 127.0.0.1 --port 8080 --session my-session

Dependencies
------------
Uses `aiohttp` (already in requirements.txt). No extra install needed.
If you prefer the `websockets` package, install it with:
    pip install websockets
and the --backend flag switches to it:
    python client/text_client.py --text "今日の東京の天気は？" --backend websockets
"""

import argparse
import asyncio
import json
import sys
import uuid
from urllib.parse import urlencode


# ---------------------------------------------------------------------------
# aiohttp backend (default)
# ---------------------------------------------------------------------------

async def _run_aiohttp(url: str, payload: dict) -> None:
    try:
        import aiohttp
    except ImportError:
        print(
            "ERROR: aiohttp is not installed. "
            "Run: pip install aiohttp  (or pip install -r requirements.txt)",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Connecting to {url} …")
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_str(json.dumps(payload))
            msg = await ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                _print_response(msg.data)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                print(f"WebSocket error: {ws.exception()}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"Unexpected message type: {msg.type}", file=sys.stderr)
                sys.exit(1)


# ---------------------------------------------------------------------------
# websockets backend (optional, install separately)
# ---------------------------------------------------------------------------

async def _run_websockets(url: str, payload: dict) -> None:
    try:
        import websockets  # type: ignore
    except ImportError:
        print(
            "ERROR: websockets is not installed. "
            "Run: pip install websockets",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Connecting to {url} …")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(payload))
        response = await ws.recv()
        _print_response(response)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _print_response(raw: str) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"Raw response: {raw}")
        return

    msg_type = data.get("type", "unknown")
    print(f"\n[{msg_type}]")

    if msg_type == "weather.response":
        print(f"  場所  : {data.get('location', '-')}")
        print(f"  日時  : {data.get('day', '-')}")
        print(f"  回答  : {data.get('text', '-')}")
        if data.get("demo_data"):
            print("  ※ このデータはデモ用のサンプルデータです（実際の気象情報ではありません）")

    elif msg_type == "weather.clarification":
        print(f"  質問  : {data.get('text', '-')}")
        missing = data.get("missing", [])
        if missing:
            print(f"  不足情報: {', '.join(missing)}")

    elif msg_type == "error":
        print(f"  エラー: {data.get('message', raw)}", file=sys.stderr)

    else:
        # Unknown type – dump full JSON so nothing is hidden
        print(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a weather.request to the local weather-forecast agent."
    )
    parser.add_argument(
        "--text",
        required=True,
        help='Weather question in Japanese, e.g. "今日の東京の天気は？"',
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Agent server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Agent server port (default: 8080)",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Session ID (default: random UUID)",
    )
    parser.add_argument(
        "--backend",
        choices=["aiohttp", "websockets"],
        default="aiohttp",
        help="WebSocket backend to use (default: aiohttp)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    session_id = args.session or str(uuid.uuid4())
    url = f"ws://{args.host}:{args.port}/invocations_ws?{urlencode({'agent_session_id': session_id})}"
    payload = {
        "type": "weather.request",
        "text": args.text,
        "session_id": session_id,
    }

    print(f"Request : {args.text}")
    print(f"Session : {session_id}")

    if args.backend == "websockets":
        asyncio.run(_run_websockets(url, payload))
    else:
        asyncio.run(_run_aiohttp(url, payload))


if __name__ == "__main__":
    main()
