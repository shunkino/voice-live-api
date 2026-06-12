# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""Integrated Voice Live experiments — single entrypoint.

Combines three Voice Live capabilities behind one configuration surface:

  1. Custom voice   (--voice-type personal|custom|standard|avatar-voice-sync)
  2. Face avatar    (--avatar, web mode)
  3. MAI-Transcribe (--transcription-model mai-transcribe-1)

Run modes:

  # Terminal conversation (voice + transcription experiments)
  python voice-live-experiments.py --mode cli --use-token-credential \
      --voice-type personal --voice my-voice --voice-base-model DragonLatestNeural \
      --transcription-model mai-transcribe-1

  # Browser with talking-head avatar (adds feature #2 on top)
  python voice-live-experiments.py --mode web --use-token-credential \
      --avatar --avatar-character lisa --transcription-model mai-transcribe-1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

from dotenv import load_dotenv

# Make emoji-bearing output safe on legacy Windows consoles (e.g. cp932).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Run from this file's directory so relative paths (logs, lexicon, static) work.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

load_dotenv("./.env", override=True)

from voicelive_demo.config import add_common_arguments, config_from_args  # noqa: E402

if not os.path.exists("logs"):
    os.makedirs("logs")

_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
logging.basicConfig(
    filename=f"logs/{_timestamp}_experiments.log",
    filemode="w",
    format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integrated Voice Live experiments: custom voice + avatar + MAI-Transcribe.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("cli", "web"),
        default=os.environ.get("AZURE_VOICELIVE_MODE", "cli"),
        help="cli = terminal mic/speaker; web = browser + avatar.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("AZURE_VOICELIVE_WEB_HOST", "127.0.0.1"),
        help="Web mode bind host.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AZURE_VOICELIVE_WEB_PORT", "8000")),
        help="Web mode bind port.",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging.")
    add_common_arguments(parser)
    return parser.parse_args()


def build_credential_factory(api_key, use_token_credential):
    """Return a callable that produces a fresh credential per session."""
    from azure.core.credentials import AzureKeyCredential

    if use_token_credential:
        from azure.identity.aio import AzureCliCredential

        return lambda: AzureCliCredential()
    return lambda: AzureKeyCredential(api_key)


def run_cli(config, credential_factory) -> None:
    from voicelive_demo.cli_assistant import CliVoiceAssistant

    assistant = CliVoiceAssistant(config, credential_factory())

    def signal_handler(_sig, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(assistant.start())
    except KeyboardInterrupt:
        print("\n👋 Voice assistant shut down. Goodbye!")
    except Exception as e:  # pragma: no cover - top-level guard
        logger.exception("Fatal error")
        print("Fatal Error:", e)


def run_web(config, credential_factory, host: str, port: int) -> None:
    try:
        import uvicorn
    except ImportError:
        print(
            "❌ Web mode needs FastAPI + uvicorn. Install with:\n"
            "   pip install -r requirements.txt"
        )
        sys.exit(1)

    from voicelive_demo.web.server import create_app

    app = create_app(config, credential_factory)
    print("\n" + "=" * 60)
    print("🌐 VOICE LIVE EXPERIMENTS — WEB")
    print(f"   {config.summary()}")
    print(f"   Open http://{host}:{port} in your browser")
    print("   Press Ctrl+C to stop")
    print("=" * 60 + "\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    args = parse_arguments()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.api_key and not args.use_token_credential:
        print("❌ Error: No authentication provided.")
        print("   Provide --api-key, set AZURE_VOICELIVE_API_KEY,")
        print("   or use --use-token-credential after 'az login'.")
        sys.exit(1)

    try:
        config = config_from_args(args)
    except ValueError as e:
        print(f"❌ Invalid configuration: {e}")
        sys.exit(1)

    credential_factory = build_credential_factory(
        args.api_key, args.use_token_credential
    )

    logger.info("Starting in %s mode: %s", args.mode, config.summary())

    if args.mode == "web":
        run_web(config, credential_factory, args.host, args.port)
    else:
        run_cli(config, credential_factory)


if __name__ == "__main__":
    print("🎙️  Voice Live Experiments — custom voice + avatar + MAI-Transcribe")
    print("=" * 60)
    main()
