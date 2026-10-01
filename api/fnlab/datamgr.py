"""
Data management for the dashboard's Data page: everything Vantage-Data.bat does, run as jobs.

A job is a list of steps (pipeline commands or small Python functions) run one job at a time, so two
jobs never touch the same files at once. Each step's output is read line by line to drive a progress
bar: the downloader prints "[23/100] ok ...", the leaderboard reader "page 3/10", the parser one
"ok/skip/FAIL" line per replay, table building one line per table.

These endpoints run programs and change files, so they only answer requests from this computer.
"""
from __future__ import annotations

import calendar
import csv
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import config

REPO = config.REPO
IS_WIN = os.name == "nt"
NODE_DIR = REPO / "pipeline" / "node"
PY_DIR = REPO / "pipeline" / "python"
EPIC_LOGIN_URL = "https://www.epicgames.com/id/api/redirect?clientId=3f69e56c7649492c8cc29f1af08a8a12&responseType=code"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

router = APIRouter(prefix="/api/data")


def local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, "Data actions are only available on this computer.")


# --------------------------------------------------------------------------- settings (.env)
def env_get(key: str) -> str | None:
    return config._env_file(key)


def env_set(key: str, value: str | None) -> None:
    path = REPO / ".env"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.partition("=")[0].strip() == key:
            if value is not None and not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if value is not None and not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def data_dir() -> Path:
    return Path(config.DATASETS["real"])


# --------------------------------------------------------------------------- jobs
@dataclass
class Step:
    label: str
    cmd: list[str] | None = None
    fn: Callable[["Job"], None] | None = None
    cwd: Path = REPO
    stdin: str | None = None
    weight: float = 1.0
    total: Callable[[], int] | None = None   # expected number of per-item lines (parser)
    allow_fail: bool = False
    when: Callable[["Job"], bool] | None = None


@dataclass
class Job:
    kind: str
    title: str
    steps: list[Step]
    params: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"                    # queued | running | done | failed | cancelled
    step: int = 0
    step_label: str = ""
    step_frac: float | None = None
    detail: str = ""
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None
    step_started: float | None = None
    error: str | None = None
    failed_steps: list[str] = field(default_factory=list)
    lines: deque = field(default_factory=lambda: deque(maxlen=400))
    proc: subprocess.Popen | None = None
    count: int = 0
    expected: int | None = None
    flags: dict = field(default_factory=dict)

    def progress(self) -> float | None:
        if self.status == "done":
            return 1.0
        weights = [s.weight for s in self.steps] or [1]
        done = sum(weights[: self.step])
        cur = weights[self.step] if self.step < len(weights) else 0
        frac = self.step_frac if self.step_frac is not None else 0.0
        return min(1.0, (done + cur * frac) / sum(weights))

    def eta_s(self) -> float | None:
        if self.step_frac and self.step_frac > 0.02 and self.step_started:
            el = time.time() - self.step_started
            return el * (1 - self.step_frac) / self.step_frac
        return None

    def summary(self) -> dict:
        return dict(id=self.id, kind=self.kind, title=self.title, status=self.status, step=self.step,
                    steps=[s.label for s in self.steps], step_label=self.step_label, detail=self.detail,
                    progress=self.progress(), step_frac=self.step_frac, eta_s=self.eta_s(), params=self.params,
                    created=self.created, started=self.started, ended=self.ended, error=self.error,
                    failed_steps=self.failed_steps)


JOBS: dict[str, Job] = {}
QUEUE: deque[str] = deque()
LOCK = threading.Lock()
WAKE = threading.Event()

PATTERNS = [
    (re.compile(r"\[(\d+)/(\d+)\]\s+(ok|FAIL)"), "Downloading match {0} of {1}"),
    (re.compile(r"leaderboard page (\d+)/(\d+)"), "Reading leaderboard page {0} of {1}"),
    (re.compile(r"power rankings page (\d+)/(\d+)"), "Reading Power Rankings page {0} of {1}"),
]
PARSER_LINE = re.compile(r"^(ok|skip|FAIL)\s+\S+")
TABLE_LINE = re.compile(r"^(\w+)\s+[\d,]+ rows")
TO_DOWNLOAD = re.compile(r"(\d+) IDs listed, (\d+) to download")


def _parse(job: Job, line: str) -> None:
    for rx, text in PATTERNS:
        m = rx.search(line)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            job.step_frac = a / b if b else None
            job.detail = text.format(a, b)
            return
    m = TO_DOWNLOAD.search(line)
    if m:
        job.detail = f"{m.group(2)} matches to download"
        if int(m.group(2)) == 0:
            job.step_frac = 1.0
        return
    if PARSER_LINE.match(line) and job.expected:
        job.count += 1
        job.step_frac = min(1.0, job.count / job.expected)
        job.detail = f"Processing replay {job.count} of {job.expected}"
        return
    m = TABLE_LINE.match(line.strip())
    if m:
        job.detail = f"Building tables: {m.group(1).replace('_', ' ')}"


def _run_cmd(job: Job, step: Step) -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", FORCE_COLOR="0")
    kw = dict(cwd=str(step.cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
              stdin=subprocess.PIPE if step.stdin is not None else subprocess.DEVNULL, env=env)
    if IS_WIN:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    job.lines.append(f"$ {' '.join(Path(step.cmd[0]).name if i == 0 else a for i, a in enumerate(step.cmd))}")
    proc = subprocess.Popen(step.cmd, **kw)
    job.proc = proc
    if step.stdin is not None:
        try:
            proc.stdin.write(step.stdin.encode())
            proc.stdin.close()
        except OSError:
            pass
    buf, last_cr = b"", False
    while True:
        chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        while True:
            idx = min([i for i in (buf.find(b"\n"), buf.find(b"\r")) if i >= 0], default=-1)
            if idx < 0:
                break
            raw, sep, buf = buf[:idx], buf[idx:idx + 1], buf[idx + 1:]
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                if last_cr and job.lines:
                    job.lines[-1] = line       # progress lines rewrite themselves with \r
                else:
                    job.lines.append(line)
                _parse(job, line)
            last_cr = sep == b"\r"
    if buf.strip():
        job.lines.append(buf.decode("utf-8", errors="replace").rstrip())
    return proc.wait()


# Known failures, explained for the dashboard (the scripts' own wording refers to the old menu).
FRIENDLY = [
    (("no longer works", "invalid_account_credentials"),
     "Your Epic sign-in has expired. Sign in again with the box at the top of this page.", "expire_login"),
    (("Not logged in to Epic",), "Sign in to Epic first, with the box at the top of this page.", None),
    (("needs rebuilding", "isn't built yet"), "The replay parser needs building: Settings and advanced tools → Rebuild the replay parser.", None),
    (("No match IDs for that window",), "Collect this tournament's matches first (Collect matches), then download.", None),
    (("ENOTFOUND", "ECONNREFUSED", "ETIMEDOUT", "fetch failed"), "Couldn't reach Epic. Check the internet connection and try again.", None),
    (("No space left", "ENOSPC"), "The data drive is full. Free some space, or move the data folder under Settings.", None),
]


def _explain(job: Job) -> None:
    text = "\n".join(list(job.lines)[-30:])
    for needles, message, action in FRIENDLY:
        if any(n in text for n in needles):
            job.error = message
            if action == "expire_login":
                auth = REPO / ".epic-auth.json"
                if auth.exists():
                    auth.replace(REPO / ".epic-auth.json.expired")  # the sign-in box reappears
            return


def _worker() -> None:
    while True:
        WAKE.wait()
        with LOCK:
            jid = QUEUE.popleft() if QUEUE else None
            if not QUEUE:
                WAKE.clear()
        if jid is None:
            continue
        job = JOBS[jid]
        if job.status == "cancelled":
            continue
        job.status, job.started = "running", time.time()
        try:
            for i, step in enumerate(job.steps):
                if job.status == "cancelled":
                    break
                job.step, job.step_label, job.step_frac = i, step.label, None
                job.step_started, job.count, job.detail = time.time(), 0, ""
                if step.when and not step.when(job):
                    job.step_frac = 1.0
                    continue
                job.expected = step.total() if step.total else None
                if step.fn:
                    step.fn(job)
                    rc = 0
                else:
                    rc = _run_cmd(job, step)
                    job.proc = None
                if job.status == "cancelled":
                    break
                job.step_frac = 1.0
                if rc != 0:
                    job.failed_steps.append(step.label)
                    if not step.allow_fail:
                        job.status, job.error = "failed", f"{step.label} stopped with an error (exit code {rc}). See the log."
                        _explain(job)
                        break
            if job.status == "running":
                job.status = "done"
                job.detail = "Finished" + (f" ({len(job.failed_steps)} optional step(s) had problems)" if job.failed_steps else "")
        except Exception as e:  # noqa: BLE001 - surface any failure on the job, keep the worker alive
            job.status, job.error = "failed", f"{e}" if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            job.lines.append(job.error)
            _explain(job)
        job.ended = time.time()


threading.Thread(target=_worker, daemon=True, name="vantage-jobs").start()


def enqueue(job: Job) -> Job:
    with LOCK:
        JOBS[job.id] = job
        QUEUE.append(job.id)
        WAKE.set()
    return job


def cancel(job: Job) -> None:
    if job.status in ("done", "failed", "cancelled"):
        return
    job.status = "cancelled"
    job.ended = time.time()
    job.detail = "Cancelled"
    p = job.proc
    if p and p.poll() is None:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            p.kill()


# --------------------------------------------------------------------------- step builders
def node(script: str, *args: str, **kw) -> Step:
    return Step(cmd=[shutil.which("node") or "node", script, *map(str, args)], cwd=NODE_DIR, **kw)


def py(script: str, *args: str, **kw) -> Step:
    return Step(cmd=[sys.executable, str(PY_DIR / script), *map(str, args)], **kw)


def extractor_path() -> Path | None:
    for p in ("pipeline/extractor/bin/source/ZoneLab.ReplayReader.dll", "pipeline/extractor/bin/nuget/ZoneLab.ReplayReader.dll"):
        if (REPO / p).exists():
            return REPO / p
    return None


def parse_step(overwrite: bool = False, only: str | None = None, weight: float = 3) -> Step:
    d = data_dir()

    def total() -> int:
        raws = [d / "raw" / f"{only}.replay"] if only else list((d / "raw").glob("*.replay"))
        return len([r for r in raws if r.exists() and (overwrite or not (d / "parsed" / f"{r.stem}.json").exists())])
    ex = extractor_path()
    if ex is None:
        def missing(job: Job) -> None:
            raise RuntimeError("The replay parser isn't built yet. Use 'Rebuild the replay parser' under Advanced.")
        return Step("Processing replays", fn=missing)
    src = d / "raw" / f"{only}.replay" if only else d / "raw"
    args = [str(src), str(d / "parsed"), "--mode", "full"] + (["--overwrite"] if overwrite else [])
    return Step("Processing replays", cmd=[shutil.which("dotnet") or "dotnet", str(ex), *args], weight=weight, total=total)


def prune_step() -> Step:
    def fn(job: Job) -> None:
        if (env_get("ZONELAB_KEEP_RAW") or "yes").lower() != "no":
            job.detail = "Keeping raw replays"
            return
        d, n, freed = data_dir(), 0, 0
        for r in (d / "raw").glob("*.replay"):
            p = d / "parsed" / f"{r.stem}.json"
            if p.exists() and p.stat().st_size > 1000:
                freed += r.stat().st_size
                r.unlink()
                n += 1
        job.lines.append(f"Deleted {n} processed raw replays, freeing {freed / 1e9:.1f} GB.")
    return Step("Tidying raw replays", fn=fn, weight=0.2)


def analyze_steps() -> list[Step]:
    d = str(data_dir())
    return [py("flatten.py", "--data-dir", d, label="Building tables", weight=1.5),
            py("validate.py", "--data-dir", d, label="Checking data quality", weight=0.3, allow_fail=True),
            py("zone_analysis.py", "--data-dir", d, label="Writing the storm report", weight=0.3, allow_fail=True)]


def _download_steps(p: dict) -> list[Step]:
    args = ["--limit", int(p.get("limit") or 100)]
    if p.get("window"):
        args += ["--window", p["window"]]
    if int(p.get("min_top") or 0) > 0:
        args += ["--min-top1000", int(p["min_top"])]
    if p.get("via") == "api-fortnite":
        args += ["--via", "api-fortnite"]
    return [node("download.js", *args, label="Downloading replays", weight=6),
            parse_step(), prune_step(), *analyze_steps()]


def build_job(kind: str, p: dict) -> Job:
    d = data_dir()
    if kind == "login":
        code = (p.get("code") or "").strip()
        if not code:
            raise HTTPException(400, "Paste the text Epic showed (it contains \"authorizationCode\").")
        return Job(kind, "Signing in to Epic", [node("epic_auth.js", "login", label="Signing in", stdin=code + "\n")], {})
    if kind == "logout":
        return Job(kind, "Signing out of Epic", [node("epic_auth.js", "logout", label="Signing out")], {})
    if kind == "tournaments":
        return Job(kind, "Refreshing the tournament list",
                   [node("find_matches.js", "tournaments", "--days", int(p.get("days") or 30), label="Reading Epic's tournament list")], p)
    if kind == "collect":
        w = (p.get("window") or "").strip()
        if not w:
            raise HTTPException(400, "Choose a tournament window.")
        pages = str(p.get("pages") or 10)
        return Job(kind, f"Collecting match IDs: {w}",
                   [node("find_matches.js", "window", w, "--pages", pages, label="Reading the leaderboard")], p)
    if kind == "download":
        w = p.get("window")
        title = f"Downloading {w}" if w else f"Downloading up to {int(p.get('limit') or 100)} matches"
        return Job(kind, title, _download_steps(p), p)
    if kind == "weekly":
        args = ["weekly", "--days", int(p.get("days") or 7), "--pages", int(p.get("pages") or 10)]
        if p.get("region"):
            args += ["--region", p["region"]]
        steps = [node("find_matches.js", *args, label="Finding this week's high-tier tournaments", weight=1.5)] + \
            _download_steps({"limit": p.get("limit") or 100, "min_top": p.get("min_top") if p.get("min_top") is not None else 3})
        if p.get("refresh_pr"):
            steps.insert(0, node("find_matches.js", "powerrankings", label="Refreshing Power Rankings", weight=1.5))
        return Job(kind, "Weekly tier-1 collection", steps, p)
    if kind == "powerrankings":
        return Job(kind, "Refreshing Power Rankings",
                   [node("find_matches.js", "powerrankings", label="Reading Power Rankings", weight=4), *analyze_steps()], p)
    if kind == "pois":
        return Job(kind, "Updating map place names", [node("pois.js", label="Downloading place names"), *analyze_steps()], p)
    if kind == "import":
        folder = Path(p.get("folder") or Path(os.environ.get("LOCALAPPDATA", "")) / "FortniteGame" / "Saved" / "Demos")
        count = int(p.get("count") or 0)

        def copy(job: Job) -> None:
            if not folder.exists():
                raise RuntimeError(f"Folder not found: {folder}")
            files = sorted(folder.rglob("*.replay"), key=lambda f: f.stat().st_mtime, reverse=True)
            files = files[:count] if count > 0 else files
            (d / "raw").mkdir(parents=True, exist_ok=True)
            new = 0
            for i, f in enumerate(files, 1):
                dest = d / "raw" / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
                    new += 1
                job.step_frac, job.detail = i / max(1, len(files)), f"Copying file {i} of {len(files)}"
            job.lines.append(f"Imported {new} new replay files ({len(files) - new} already there) from {folder}")
        return Job(kind, "Importing replay files", [Step("Copying replay files", fn=copy), parse_step(), *analyze_steps()], p)
    if kind == "reprocess":
        mid = (p.get("match_id") or "").strip() or None
        return Job(kind, f"Re-processing {mid}" if mid else "Re-processing every match",
                   [parse_step(overwrite=True, only=mid, weight=5), *analyze_steps()], p)
    if kind == "analyze":
        return Job(kind, "Rebuilding tables", analyze_steps(), p)
    if kind == "survey":
        ex = extractor_path()
        if ex is None:
            raise HTTPException(400, "Build the replay parser first.")
        raws = sorted((d / "raw").glob(f"{p.get('match_id') or ''}*.replay"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not raws:
            raise HTTPException(400, "No downloaded replay to survey (raw replays may have been deleted after processing).")
        out = d / "reports" / "survey"
        out.mkdir(parents=True, exist_ok=True)
        return Job(kind, f"Surveying {raws[0].stem}",
                   [Step("Listing every kind of data in the replay",
                         cmd=[shutil.which("dotnet") or "dotnet", str(ex), str(raws[0]), str(out), "--survey", "--overwrite"])], p)
    if kind == "genexports":
        surveys = sorted((d / "reports" / "survey").glob("*.survey.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not surveys:
            raise HTTPException(400, "Run a survey first.")
        args = [str(surveys[0])]
        src = REPO / "pipeline" / "extractor" / "vendor" / "FortniteReplayDecompressor" / "src"
        if src.exists():
            args += ["--parser-src", str(src)]
        return Job(kind, "Updating the parser's definitions", [py("gen_exports.py", *args, label="Writing definitions")], p)
    if kind == "rebuild_parser":
        dotnet = shutil.which("dotnet") or "dotnet"
        vendor = REPO / "pipeline" / "extractor" / "vendor" / "FortniteReplayDecompressor"
        nuget = Step("Building the parser (published package)", cmd=[dotnet, "build", "pipeline/extractor/fn-extract.csproj", "-c", "Release"],
                     allow_fail=True, weight=2)
        git = shutil.which("git") or "git"
        fetch = Step("Fetching the parser's source", weight=1,
                     cmd=[git, "-C", str(vendor), "pull", "--ff-only"] if vendor.exists() else
                     [git, "clone", "--depth", "1", "https://github.com/Shiqan/FortniteReplayDecompressor.git", str(vendor)],
                     when=lambda j: "Building the parser (published package)" in j.failed_steps)
        source = Step("Building the parser from source", weight=2,
                      cmd=[dotnet, "build", "pipeline/extractor/source/fn-extract.source.csproj", "-c", "Release"],
                      when=lambda j: "Building the parser (published package)" in j.failed_steps)
        return Job(kind, "Rebuilding the replay parser", [nuget, fetch, source], p)
    raise HTTPException(400, f"Unknown job: {kind}")


# --------------------------------------------------------------------------- endpoints
class JobRequest(BaseModel):
    kind: str
    params: dict = Field(default_factory=dict)


@router.post("/jobs")
def create_job(req: JobRequest, request: Request):
    local_only(request)
    return enqueue(build_job(req.kind, req.params)).summary()


@router.get("/jobs")
def list_jobs(request: Request):
    local_only(request)
    jobs = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)[:20]
    return [j.summary() for j in jobs]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, lines: int = 200):
    local_only(request)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job (the service may have restarted).")
    return {**job.summary(), "log": list(job.lines)[-lines:]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    local_only(request)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    cancel(job)
    return job.summary()


def _csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@router.get("/status")
def status(request: Request):
    local_only(request)
    d = data_dir()
    auth = REPO / ".epic-auth.json"
    epic = {"logged_in": auth.exists()}
    if auth.exists():
        try:
            import json
            a = json.loads(auth.read_text(encoding="utf-8"))
            epic.update(name=a.get("displayName"), account_id=a.get("accountId"), since=a.get("created"))
        except (OSError, ValueError):
            pass
    ids = _csv_rows(d / "match_ids.csv")
    raw = {f.stem for f in (d / "raw").glob("*.replay")} if (d / "raw").exists() else set()
    parsed = {f.stem for f in (d / "parsed").glob("*.json")} if (d / "parsed").exists() else set()
    in_tables = 0
    mt = d / "tables" / "matches.parquet"
    if mt.exists():
        try:
            import pyarrow.parquet as pq
            in_tables = pq.ParquetFile(mt).metadata.num_rows
        except Exception:  # noqa: BLE001
            in_tables = 0
    pr = _csv_rows(d / "power_rankings.csv")
    pois = _csv_rows(d / "pois.csv")
    validation = None
    vm = d / "reports" / "validation.md"
    if vm.exists():
        validation = next((l.strip() for l in vm.read_text(encoding="utf-8", errors="replace").splitlines() if "pass" in l and "matches" in l), None)
    ids_set = {r.get("match_id", "").lower() for r in ids}
    waiting = len([i for i in ids_set if i and i not in raw and i not in parsed])
    tl = d / "tournaments.csv"
    return dict(
        epic=epic, login_url=EPIC_LOGIN_URL,
        parser=dict(ready=extractor_path() is not None, dotnet=shutil.which("dotnet") is not None),
        data_dir=str(d), data_exists=d.exists(), size_gb=round(_dir_size(d) / 1e9, 2) if d.exists() else 0,
        keep_raw=(env_get("ZONELAB_KEEP_RAW") or "yes").lower() != "no",
        api_key_set=bool(env_get("FORTNITE_API_KEY")),
        counts=dict(collected=len(ids_set), waiting=waiting, raw=len(raw), parsed=len(parsed), in_tables=in_tables),
        power_rankings=dict(players=len(pr), fetched=pr[0].get("fetched") if pr else None),
        pois=dict(places=len(pois), fetched=pois[0].get("fetched") if pois else None),
        validation=validation,
        tournaments_list=dict(windows=len(_csv_rows(tl)), updated=time.strftime("%Y-%m-%d %H:%M", time.localtime(tl.stat().st_mtime)) if tl.exists() else None),
        busy=any(j.status in ("queued", "running") for j in JOBS.values()),
    )


@router.get("/tournaments")
def tournaments(request: Request, search: str = "", region: str = "", days: int = 30, upcoming: bool = False):
    local_only(request)
    d = data_dir()
    rows = _csv_rows(d / "tournaments.csv")
    ids = _csv_rows(d / "match_ids.csv")
    parsed = {f.stem for f in (d / "parsed").glob("*.json")} if (d / "parsed").exists() else set()
    raw = {f.stem for f in (d / "raw").glob("*.replay")} if (d / "raw").exists() else set()
    per: dict[str, list[str]] = {}
    for r in ids:
        per.setdefault(r.get("event_window_id", ""), []).append(r.get("match_id", "").lower())
    now = time.time()
    out = []
    q = search.lower().strip()
    for r in rows:
        end = r.get("end") or ""
        try:
            t_end = calendar.timegm(time.strptime(end[:19].replace(" ", "T"), "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        if upcoming != (t_end > now):
            continue
        if not upcoming and t_end < now - days * 86400:
            continue
        wid = r.get("event_window_id", "")
        if q and q not in wid.lower() and q not in (r.get("name") or "").lower():
            continue
        if region and (r.get("region") or "").upper() != region.upper():
            continue
        m = per.get(wid, [])
        out.append(dict(end=end, region=r.get("region") or "", name=r.get("name") or wid, window=wid, event=r.get("event_id"),
                        collected=len(m), downloaded=len([i for i in m if i in raw or i in parsed]),
                        processed=len([i for i in m if i in parsed])))
    out.sort(key=lambda x: x["end"], reverse=not upcoming)
    return dict(rows=out[:400], total=len(out))


@router.get("/plan")
def plan(request: Request, limit: int = 15, min_top: int = 0):
    """Preview what a download would take next (strongest lobbies first), without downloading."""
    local_only(request)
    args = [shutil.which("node") or "node", "download.js", "--plan", "--limit", str(limit)]
    if min_top > 0:
        args += ["--min-top1000", str(min_top)]
    r = subprocess.run(args, cwd=NODE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    rows = []
    for line in r.stdout.splitlines():
        m = re.match(r"^([0-9a-f]{32})\s+(\d+)\s+(\d+)\s+(\d+)\s*$", line.strip())
        if m:
            rows.append(dict(match_id=m.group(1), top1000=int(m.group(2)), top10000=int(m.group(3)), seen=int(m.group(4))))
    waiting = re.search(r"Next \d+ of (\d+) waiting", r.stdout)
    return dict(rows=rows, waiting=int(waiting.group(1)) if waiting else len(rows),
                note=next((l for l in r.stdout.splitlines() if l.startswith(("Strongest", "No lobby", "--min"))), ""))


class Settings(BaseModel):
    data_dir: str | None = None
    keep_raw: bool | None = None
    fortnite_api_key: str | None = None


@router.put("/settings")
def put_settings(s: Settings, request: Request):
    local_only(request)
    if s.data_dir is not None:
        path = Path(s.data_dir.strip() or REPO / "data")
        path.mkdir(parents=True, exist_ok=True)
        env_set("ZONELAB_DATA_DIR", str(path) if s.data_dir.strip() else None)
        config.DATASETS["real"] = path
    if s.keep_raw is not None:
        env_set("ZONELAB_KEEP_RAW", "yes" if s.keep_raw else "no")
    if s.fortnite_api_key is not None:
        env_set("FORTNITE_API_KEY", s.fortnite_api_key.strip() or None)
    return {"ok": True}
