"""
The real Fortnite map image for the interactive match map, and its calibration.

The image (saved by the map download as map.png / map_pois.png in the data folder) uses pixels; the game uses
its own coordinates. Clicking three named places on the image gives three (pixel, game) pairs, from which an
affine transform is fitted (it handles any rotation or flip between the two).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config
from .datamgr import local_only

router = APIRouter(prefix="/api/map")


def _data() -> Path:
    return Path(config.DATASETS["real"])


def calibration() -> dict | None:
    """{'image_to_game': 2x3 matrix, 'game_to_image': 2x3 matrix, 'image': name} or None."""
    p = _data() / "map_calibration.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


@router.get("/image")
def image(kind: str = "plain"):
    f = _data() / ("map_pois.png" if kind == "pois" else "map.png")
    if not f.exists():
        raise HTTPException(404, "No map image yet: run Data → Update map names.")
    return FileResponse(f, media_type="image/png")


@router.get("/info")
def info():
    d = _data()
    return {"plain": (d / "map.png").exists(), "labelled": (d / "map_pois.png").exists(), "calibration": calibration()}


class Point(BaseModel):
    px: float
    py: float
    x: float
    y: float


class Calibration(BaseModel):
    points: list[Point]


@router.put("/calibration")
def save_calibration(c: Calibration, request: Request):
    local_only(request)
    if len(c.points) < 3:
        raise HTTPException(400, "Click at least three places.")
    P = np.array([[p.px, p.py, 1.0] for p in c.points])
    G = np.array([[p.x, p.y] for p in c.points])
    img_to_game, *_ = np.linalg.lstsq(P, G, rcond=None)            # 3x2: [px, py, 1] -> [x, y]
    Gh = np.c_[G, np.ones(len(G))]
    game_to_img, *_ = np.linalg.lstsq(Gh, P[:, :2], rcond=None)
    resid = np.hypot(*(P @ img_to_game - G).T) / 100
    if len(c.points) >= 3 and abs(np.linalg.det(img_to_game[:2])) < 1e-9:
        raise HTTPException(400, "Those places are in a line; pick three spread across the map.")
    out = {"image_to_game": img_to_game.T.tolist(), "game_to_image": game_to_img.T.tolist(),
           "points": [p.model_dump() for p in c.points], "error_m": float(resid.max())}
    (_data() / "map_calibration.json").write_text(json.dumps(out, indent=1))
    return out


@router.delete("/calibration")
def clear_calibration(request: Request):
    local_only(request)
    p = _data() / "map_calibration.json"
    if p.exists():
        p.unlink()
    return {"ok": True}
