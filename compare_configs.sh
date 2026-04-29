#!/bin/bash
# compare_configs.sh - Compare different Voice Live configurations
#
# Usage: bash compare_configs.sh
#
# This script launches voice-live-function-call.py with different
# parameter combinations so you can subjectively evaluate:
#   - Japanese TTS naturalness (rate, temperature)
#   - Reasoning capability (model selection)
#   - STT accuracy (phrase_list)
#
# Press Ctrl+C to stop each run, then the next config starts.

set -e

SCRIPT="voice-live-function-call.py"

echo "============================================="
echo " Voice Live Configuration Comparison"
echo "============================================="
echo ""
echo "Each configuration will start a voice session."
echo "Press Ctrl+C to stop and move to the next config."
echo ""

# --- Config 1: Baseline (current defaults) ---
echo ">>> Config 1: Baseline (gpt-realtime, rate=1.0, temp=0.8)"
echo "    No phrase_list (not supported by gpt-realtime)"
python "$SCRIPT" --model gpt-realtime --rate "1.0" --temperature 0.8 || true
echo ""

# --- Config 2: Slower rate for natural Japanese ---
echo ">>> Config 2: Slower rate (gpt-realtime, rate=0.9, temp=0.8)"
python "$SCRIPT" --model gpt-realtime --rate "0.9" --temperature 0.8 || true
echo ""

# --- Config 3: Higher expressiveness ---
echo ">>> Config 3: High expressiveness (gpt-realtime, rate=0.95, temp=1.0)"
python "$SCRIPT" --model gpt-realtime --rate "0.95" --temperature 1.0 || true
echo ""

# --- Config 4: GPT-5 with phrase_list (if available) ---
echo ">>> Config 4: GPT-5 cascade mode (rate=0.95, temp=0.8, phrase_list enabled)"
echo "    This uses Azure STT+TTS cascade for stronger reasoning."
python "$SCRIPT" --model gpt-5 --rate "0.95" --temperature 0.8 || true
echo ""

# --- Config 5: GPT-4.1 with phrase_list ---
echo ">>> Config 5: GPT-4.1 cascade mode (rate=0.95, temp=0.8, phrase_list enabled)"
python "$SCRIPT" --model gpt-4.1 --rate "0.95" --temperature 0.8 || true
echo ""

echo "============================================="
echo " All configurations tested."
echo "============================================="
echo ""
echo "Recommendations:"
echo "  - For best Japanese naturalness: rate=0.9~0.95, temp=0.8~1.0"
echo "  - For stronger reasoning: use gpt-5 or gpt-4.1 (cascade mode)"
echo "  - For best STT accuracy: add --phrase-list with domain terms"
