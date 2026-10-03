"""
Three fixes: the map image fit (similarity, honest error, old calibrations refitted), surge detected on a single duo or a
lone player by its rhythm, and the engine never giving healing as advice on its own.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))


def _true_map():
    th, s = 0.03, 85.0      # image y runs down (a flip), slight rotation, 85 cm per pixel
    M = s * np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]]) @ np.diag([1, -1])
    return M, np.array([-130_000.0, 125_000.0])


def test_similarity_fit_recovers_a_flipped_rotated_map():
    from fnlab.mapapi import fit_similarity
    M, t = _true_map()
    px = np.array([[150, 200], [1800, 300], [900, 1700], [400, 1200], [1500, 1500]], float)
    a, inv = fit_similarity(px, px @ M.T + t)
    assert np.allclose(a[:, :2], M, atol=1e-6) and np.allclose(a[:, 2], t, atol=1e-3)
    g = np.array([10_000.0, -20_000.0])
    back = inv[:, :2] @ (a[:, :2] @ [700, 800] + a[:, 2]) + inv[:, 2]
    assert np.allclose(back, [700, 800], atol=1e-6) and np.allclose(a[:, :2] @ (inv[:, :2] @ g + inv[:, 2]) + a[:, 2], g, atol=1e-3)


def test_error_is_measured_on_places_left_out(tmp_path, monkeypatch):
    """Three slightly-off clicks: the old fit reported 0 m; the left-out check reports the real error."""
    from fnlab import config, mapapi
    monkeypatch.setitem(config.DATASETS, "real", tmp_path)
    M, t = _true_map()
    px = np.array([[150, 200], [1800, 300], [900, 1700]], float)
    g = px @ M.T + t
    clicked = px + [[4, -3], [-5, 2], [3, 5]]                          # a few pixels off, as real clicks are
    pts = [dict(px=float(a), py=float(b), x=float(x), y=float(y)) for (a, b), (x, y) in zip(clicked, g)]
    out = mapapi._fit(pts, "plain")
    assert out["model"] == "similarity" and out["error_m"] > 1 and len(out["errors_m"]) == 3


def test_old_affine_calibration_is_refitted_on_load(tmp_path, monkeypatch):
    from fnlab import config, mapapi
    monkeypatch.setitem(config.DATASETS, "real", tmp_path)
    M, t = _true_map()
    px = np.array([[150, 200], [1800, 300], [900, 1700], [400, 1200]], float)
    g = px @ M.T + t
    old = {"image_to_game": [[1, 0, 0], [0, 1, 0]], "game_to_image": [[1, 0, 0], [0, 1, 0]], "image": "plain", "error_m": 0.0,
           "points": [dict(px=float(a), py=float(b), x=float(x), y=float(y)) for (a, b), (x, y) in zip(px, g)]}
    (tmp_path / "map_calibration.json").write_text(json.dumps(old))
    c = mapapi.calibration()
    assert c["model"] == "similarity" and np.allclose(np.array(c["image_to_game"])[:, :2], M, atol=1e-6)


def test_surge_rhythm_catches_a_lone_player_but_not_storm_or_falls():
    from fnlab.analyses.surge import surge_drops
    rows = [("m", 1, t) for t in (300.0, 305.0, 310.0)]               # a lone player, surge every 5 s
    rows += [("m", 2, t) for t in (400.0, 401.0, 402.0, 403.0)]       # storm-like, every second (misread as inside)
    rows += [("m", 3, 500.0)]                                         # a single fall
    rows += [("m", i, 600.0) for i in (4, 5, 6)]                      # three players in the same second
    d = pd.DataFrame(rows, columns=["match_id", "id", "t"])
    flagged = surge_drops(d)
    assert flagged[d["id"] == 1].all()
    assert not flagged[d["id"].isin([2, 3])].any()
    assert flagged[d["id"].isin([4, 5, 6])].all()


def test_engine_never_advises_healing_on_its_own():
    from fnlab import engine
    assert "heal" not in engine.ACTIONS and engine.ACTIONS["hold"].startswith("Hold")
