"""Tests for autocut.color — color grading preset definitions."""

from __future__ import annotations

import pytest

from autocut.color import COLOR_PRESETS, VALID_PRESETS


def test_all_presets_are_strings():
    for name, filt in COLOR_PRESETS.items():
        assert isinstance(filt, str), f"{name} filter is not a string"


def test_presets_start_with_eq():
    for name, filt in COLOR_PRESETS.items():
        assert filt.startswith("eq="), f"{name} filter does not start with 'eq='"


def test_known_presets_present():
    for name in ("warm", "cool", "cinematic", "vibrant"):
        assert name in COLOR_PRESETS


def test_valid_presets_includes_none():
    assert "none" in VALID_PRESETS


def test_valid_presets_includes_all_color_presets():
    for name in COLOR_PRESETS:
        assert name in VALID_PRESETS


def test_no_preset_for_none_key():
    assert "none" not in COLOR_PRESETS


def test_warm_boosts_gamma_r():
    warm = COLOR_PRESETS["warm"]
    assert "gamma_r" in warm


def test_cool_boosts_gamma_b():
    cool = COLOR_PRESETS["cool"]
    assert "gamma_b" in cool


def test_cinematic_reduces_saturation():
    cin = COLOR_PRESETS["cinematic"]
    # saturation < 1 → desaturated
    import re
    m = re.search(r"saturation=([\d.]+)", cin)
    assert m is not None
    assert float(m.group(1)) < 1.0


def test_vibrant_boosts_saturation():
    vib = COLOR_PRESETS["vibrant"]
    import re
    m = re.search(r"saturation=([\d.]+)", vib)
    assert m is not None
    assert float(m.group(1)) > 1.0


def test_all_presets_have_contrast():
    for name, filt in COLOR_PRESETS.items():
        assert "contrast=" in filt, f"{name} has no contrast setting"
