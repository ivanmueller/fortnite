"""
The real Fortnite map image for the interactive match map, and its calibration.

The image (saved by the map download as map.png / map_pois.png in the data folder) uses pixels; the game uses
its own coordinates. Clicking named places (or dragging zone circles) on the image gives (pixel, game) pairs, from
which a similarity transform is fitted: one uniform scale, a rotation or flip, and a shift. A top-down map image is
exactly that, so the fit can't shear or stretch one axis to absorb a slightly-off click (the old affine fit could,
and with three clicks it matched them exactly while drifting everywhere else). The error is checked by leaving each
place out of the fit in turn and measuring how far off the fit puts it: an honest error, not a fit to itself.
The zones and player paths are never moved: they're drawn in the replay's own game coordinates.
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


def fit_similarity(px: np.ndarray, g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Image pixels (n, 2) -> game cm (n, 2) by uniform scale, rotation or flip, and shift (least squares, 2+ points).
    Returns (image_to_game, game_to_image) as 2x3 matrices [[a, b, c], [d, e, f]] applied to [x, y, 1]."""
    pm, gm = px.mean(0), g.mean(0)
    P, G = px - pm, g - gm
    n2 = float((P ** 2).sum()) or 1.0
    best = None
    for flip in (1.0, -1.0):
        Q = P * [1.0, flip]
        a = float((Q[:, 0] * G[:, 0] + Q[:, 1] * G[:, 1]).sum()) / n2
        b = float((Q[:, 0] * G[:, 1] - Q[:, 1] * G[:, 0]).sum()) / n2
        M = np.array([[a, -b], [b, a]]) @ np.diag([1.0, flip])
        res = float(((P @ M.T - G) ** 2).sum())
        if best is None or res < best[0]:
            best = (res, M)
    M = best[1]
    t = gm - M @ pm
    Minv = np.linalg.inv(M)
    return np.c_[M, t], np.c_[Minv, -Minv @ t]


def _fit(points: list[dict], image: str) -> dict:
    P = np.array([[p["px"], p["py"]] for p in points], float)
    G = np.array([[p["x"], p["y"]] for p in points], float)
    img_to_game, game_to_img = fit_similarity(P, G)
    if len(points) >= 3:      # leave each place out, fit on the rest, and see how far off it lands
        loo = []
        for i in range(len(points)):
            keep = np.arange(len(points)) != i
            a, _ = fit_similarity(P[keep], G[keep])
            loo.append(float(np.hypot(*(a[:, :2] @ P[i] + a[:, 2] - G[i]))) / 100)
        error = max(loo)
    else:
        error, loo = None, []
    return {"image_to_game": img_to_game.tolist(), "game_to_image": game_to_img.tolist(), "image": image, "points": points,
            "error_m": error, "errors_m": [round(e, 1) for e in loo], "model": "similarity",
            "mismatch": len(points) >= 4 and error is not None and error > MISMATCH_M}


def calibration() -> dict | None:
    """{'image_to_game': 2x3 matrix, 'game_to_image': 2x3 matrix, 'image': name, 'error_m', ...} or None.
    A calibration saved by the older affine fit is refitted from its saved places, so it improves without re-clicking."""
    p = _data() / "map_calibration.json"
    if not p.exists():
        return None
    try:
        c = json.loads(p.read_text())
    except ValueError:
        return None
    if c.get("model") != "similarity" and len(c.get("points") or []) >= 2:
        try:
            c = _fit(c["points"], c.get("image", "plain"))
        except (KeyError, np.linalg.LinAlgError):
            pass
    return c


IMAGES = {"plain": "map.png", "pois": "map_pois.png", "custom": "map_custom.img"}
MISMATCH_M = 60        # places off by more than this after calibration: probably a different island


@router.get("/image")
def image(kind: str = "plain"):
    f = _data() / IMAGES.get(kind, "map.png")
    if not f.exists():
        raise HTTPException(404, "No map image yet: run Data → Update map names, or upload one.")
    head = f.read_bytes()[:4]
    media = "image/jpeg" if head[:3] == b"\xff\xd8\xff" else "image/webp" if head == b"RIFF" else "image/png"
    return FileResponse(f, media_type=media)


@router.put("/upload")
async def upload(request: Request):
    """Your own map image (PNG, JPEG or WebP), for when the downloaded one is a different island."""
    local_only(request)
    body = await request.body()
    if len(body) < 1000 or not (body[:8] == b"\x89PNG\r\n\x1a\n" or body[:3] == b"\xff\xd8\xff" or body[:4] == b"RIFF"):
        raise HTTPException(400, "That isn't a PNG, JPEG or WebP image.")
    if len(body) > 40_000_000:
        raise HTTPException(400, "That image is over 40 MB.")
    (_data() / IMAGES["custom"]).write_bytes(body)
    p = _data() / "map_calibration.json"
    if p.exists() and json.loads(p.read_text()).get("image") != "custom":
        p.unlink()                      # a calibration for another image no longer applies
    return {"ok": True}


@router.get("/places")
def places():
    """Every named place and landmark (game coordinates, cm), to click when calibrating the map image."""
    import csv
    f = _data() / "pois.csv"
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r_ in rows:
        try:
            out.append({"name": r_["name"], "x": float(r_["x"]), "y": float(r_["y"]), "kind": r_.get("kind") or "poi"})
        except (KeyError, ValueError):
            continue
    return sorted(out, key=lambda p: (p["kind"] != "poi", p["name"].lower()))


@router.get("/info")
def info():
    d = _data()
    return {"plain": (d / "map.png").exists(), "labelled": (d / "map_pois.png").exists(), "custom": (d / IMAGES["custom"]).exists(),
            "calibration": calibration()}


class Point(BaseModel):
    px: float
    py: float
    x: float
    y: float


class Calibration(BaseModel):
    points: list[Point]
    image: str = "plain"        # which image was clicked and will be drawn: plain (downloaded) or custom (uploaded)


@router.put("/calibration")
def save_calibration(c: Calibration, request: Request):
    local_only(request)
    if len(c.points) < 3:
        raise HTTPException(400, "Click at least three places.")
    pts = [p.model_dump() for p in c.points]
    spread = np.array([[p["px"], p["py"]] for p in pts], float)
    if np.ptp(spread[:, 0]) + np.ptp(spread[:, 1]) < 1e-6:
        raise HTTPException(400, "Those places are all at one spot; pick three spread across the map.")
    try:
        out = _fit(pts, c.image)
    except np.linalg.LinAlgError:
        raise HTTPException(400, "Those places don't fix the map's position; pick three spread across the map.")
    (_data() / "map_calibration.json").write_text(json.dumps(out, indent=1))
    return out


@router.delete("/calibration")
def clear_calibration(request: Request):
    local_only(request)
    p = _data() / "map_calibration.json"
    if p.exists():
        p.unlink()
    return {"ok": True}
