# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""Integrated Voice Live API experiments.

A single, configurable sample that combines three Voice Live capabilities:

* **Custom voice** – speak with a standard / HD / custom / personal voice.
* **Face avatar** – render a talking-head avatar (web front-end, WebRTC).
* **MAI-Transcribe-1** – swappable input transcription model for comparison.

All three are driven by one :class:`~voicelive_demo.config.ExperimentConfig`
and built into a single session by :mod:`voicelive_demo.session_factory`, so the
features compose instead of living in isolated scripts.
"""

from .config import AvatarSpec, ExperimentConfig, VoiceSpec

__all__ = ["ExperimentConfig", "VoiceSpec", "AvatarSpec"]
