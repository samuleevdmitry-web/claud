"""Ключи Вордстата из wordstat.toml: бренд, рынок, направления и категории каталога."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Keyword:
    phrase: str
    kind: str  # brand | market | category
    direction: str = ""
    category: str = ""


@dataclass(frozen=True)
class Region:
    id: int
    name: str


@dataclass(frozen=True)
class KeywordConfig:
    regions: dict[str, Region]  # сайт → регион Вордстата
    keywords: list[Keyword]

    @property
    def phrases(self) -> list[str]:
        """Уникальные фразы в порядке файла: одна фраза может входить в несколько категорий."""
        return list(dict.fromkeys(k.phrase for k in self.keywords))

    @property
    def region_ids(self) -> list[int]:
        return list(dict.fromkeys(r.id for r in self.regions.values()))


def load_keywords(path: str | Path) -> KeywordConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    regions = {site: Region(int(r["id"]), r["name"]) for site, r in data.get("regions", {}).items()}
    keywords = [Keyword(p, "brand") for p in data.get("brand", {}).get("phrases", [])]
    keywords += [Keyword(p, "market") for p in data.get("market", {}).get("phrases", [])]
    for direction in data.get("directions", []):
        for category in direction.get("categories", []):
            keywords += [Keyword(p, "category", direction["name"], category["name"]) for p in category["phrases"]]
    return KeywordConfig(regions, keywords)
