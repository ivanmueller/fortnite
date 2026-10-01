"""Match selection. Every analysis runs on the set of matches these filters select."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class Filters(BaseModel):
    dataset: Literal["real", "local", "demo"] = "demo"
    date_from: date | None = None
    date_to: date | None = None
    seasons: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    event_windows: list[str] = Field(default_factory=list)
    playlist_contains: str | None = None
    server_only: bool = False

    def where(self) -> tuple[str, list]:
        """SQL WHERE clause (without the keyword) over the matches table, plus parameters."""
        sql, params = ["TRUE"], []
        if self.date_from:
            sql.append("TRY_CAST(match_date AS DATE) >= ?")
            params.append(self.date_from)
        if self.date_to:
            sql.append("TRY_CAST(match_date AS DATE) <= ?")
            params.append(self.date_to)
        for col, values in (("season", self.seasons), ("region", self.regions), ("event_window_id", self.event_windows)):
            if values:
                sql.append(f"{col} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if self.playlist_contains:
            sql.append("lower(playlist) LIKE ?")
            params.append(f"%{self.playlist_contains.lower()}%")
        if self.server_only:
            sql.append("is_server_replay IS TRUE")
        return " AND ".join(sql), params

    def describe(self) -> str:
        parts = []
        if self.date_from or self.date_to:
            parts.append(f"{self.date_from or '…'} to {self.date_to or '…'}")
        if self.seasons:
            parts.append(", ".join(self.seasons))
        if self.regions:
            parts.append(", ".join(self.regions))
        if self.event_windows:
            parts.append(f"{len(self.event_windows)} event windows")
        if self.playlist_contains:
            parts.append(f"playlist ~ {self.playlist_contains}")
        if self.server_only:
            parts.append("server replays only")
        return "; ".join(parts) or "all matches"
