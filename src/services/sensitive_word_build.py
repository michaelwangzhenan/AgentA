"""从 houbb/sensitive-word-data 裁剪生成 AgentA 本地敏感词库。

供 tools/cli/sensitive_word_cli.py 调用；也可在 UT 中直接测本模块。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

# 进程内 HTTP 代理（build 联网拉取时用）；UT 可 configure_http_proxy(None) 清空
_http_proxies: dict[str, str] | None = None

def normalize_proxy_url(url: str) -> str:
    """补全代理 URL：无 scheme 时默认 http://；支持 host:port 简写。"""
    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        url = f"http://{url}"
    return url


def proxy_endpoint_hint(url: str) -> str:
    """返回 host:port 便于排错。"""
    p = urlparse(normalize_proxy_url(url))
    host = p.hostname or "?"
    port = p.port or (443 if p.scheme == "https" else 80)
    return f"{host}:{port}"


def proxy_port_warning(url: str) -> str | None:
    """未显式写端口时给出提示（http 默认连 80，常被误用）。"""
    p = urlparse(normalize_proxy_url(url))
    if p.hostname and p.port is None:
        return (
            f"代理未写端口，将连接 {proxy_endpoint_hint(url)}。"
            f"公司代理通常需带端口，如 http://{p.hostname}:7890"
        )
    return None


def resolve_http_proxy(cli_proxy: str | None = None) -> dict[str, str] | None:
    """解析代理 URL：CLI --proxy 优先，否则读 HTTPS_PROXY / HTTP_PROXY 环境变量。"""
    url = normalize_proxy_url((cli_proxy or "").strip())
    if not url:
        url = normalize_proxy_url(
            (
                os.getenv("HTTPS_PROXY")
                or os.getenv("https_proxy")
                or os.getenv("HTTP_PROXY")
                or os.getenv("http_proxy")
                or ""
            ).strip()
        )
    if not url:
        return None
    return {"http": url, "https": url}


def configure_http_proxy(cli_proxy: str | None = None) -> dict[str, str] | None:
    """设置本次 build 使用的 HTTP 代理；返回解析结果供日志展示。"""
    global _http_proxies
    _http_proxies = resolve_http_proxy(cli_proxy)
    return _http_proxies


def current_http_proxy() -> dict[str, str] | None:
    return _http_proxies


# houbb 内置标签编号 → AgentA deny.tsv 分类名
TAG_NAMES: dict[str, str] = {
    "0": "politics",
    "1": "drugs",
    "2": "porn",
    "3": "gambling",
    "4": "illegal",
}
# 裁剪时按优先级取词（公安备案场景政治类优先）
TAG_PRIORITY: tuple[str, ...] = ("0", "4", "1", "2", "3")

UPSTREAM_REPO = "houbb/sensitive-word-data"
UPSTREAM_BRANCH = "main"
UPSTREAM_PATHS = {
    "tags": "src/main/resources/sensitive_word_tags.txt",
    "allow": "src/main/resources/sensitive_word_allow.txt",
    "dict": "src/main/resources/sensitive_word_dict.txt",
    "license": "LICENSE.txt",
}
_NAME_TO_PRIORITY = {TAG_NAMES[t]: i for i, t in enumerate(TAG_PRIORITY)}


@dataclass
class BuildStats:
    extra_count: int = 0
    upstream_selected: int = 0
    upstream_skipped_dup: int = 0
    upstream_skipped_tag: int = 0
    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class WordEntry:
    word: str
    category: str


def _normalize_key(word: str) -> str:
    return re.sub(r"\s+", "", word.strip().lower())


def _tags_to_category(raw_tags: str) -> str:
    ids = [t.strip() for t in raw_tags.split(",") if t.strip()]
    if not ids:
        return "misc"
    names: list[str] = []
    for tid in ids:
        name = TAG_NAMES.get(tid)
        if name and name not in names:
            names.append(name)
    if not names:
        return "misc"
    # 多标签按 TAG_PRIORITY 排序后拼接
    order = {t: i for i, t in enumerate(TAG_PRIORITY)}
    ids_sorted = sorted(ids, key=lambda x: order.get(x, 99))
    ordered_names = []
    for tid in ids_sorted:
        name = TAG_NAMES.get(tid)
        if name and name not in ordered_names:
            ordered_names.append(name)
    return ",".join(ordered_names)


def parse_tags_lines(lines: Iterable[str], *, include_tags: set[str] | None) -> list[WordEntry]:
    entries: list[WordEntry] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 上游格式：词条 0  或  词条 1,4（最后一列是标签）
        parts = line.rsplit(maxsplit=1)
        if len(parts) != 2:
            continue
        word, raw_tags = parts[0].strip(), parts[1].strip()
        if not word:
            continue
        tag_ids = {t.strip() for t in raw_tags.split(",") if t.strip()}
        if include_tags is not None and not (tag_ids & include_tags):
            continue
        entries.append(WordEntry(word=word, category=_tags_to_category(raw_tags)))
    return entries


def parse_extra_tsv(path: Path) -> list[WordEntry]:
    if not path.is_file():
        return []
    entries: list[WordEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        word, category = parts[0].strip(), parts[1].strip()
        if word and category:
            entries.append(WordEntry(word=word, category=category))
    return entries


def parse_dict_lines(lines: Iterable[str]) -> list[WordEntry]:
    entries: list[WordEntry] = []
    for line in lines:
        word = line.strip()
        if word and not word.startswith("#"):
            entries.append(WordEntry(word=word, category="misc"))
    return entries


def select_entries(
    upstream: list[WordEntry],
    *,
    extras: list[WordEntry],
    max_words: int,
) -> tuple[list[WordEntry], BuildStats]:
    stats = BuildStats(extra_count=len(extras))
    seen: set[str] = set()
    selected: list[WordEntry] = []

    def _take(entry: WordEntry) -> bool:
        key = _normalize_key(entry.word)
        if not key or key in seen:
            return False
        seen.add(key)
        selected.append(entry)
        stats.by_category[entry.category] = stats.by_category.get(entry.category, 0) + 1
        return True

    for e in extras:
        _take(e)

    # 上游按标签优先级 + 词条长度（短词优先，覆盖面更广）
    def _sort_key(e: WordEntry) -> tuple[int, int, str]:
        primary = e.category.split(",")[0]
        pri = _NAME_TO_PRIORITY.get(primary, 99)
        return (pri, len(e.word), e.word)

    upstream_sorted = sorted(upstream, key=_sort_key)
    budget = max(0, max_words - len(selected))
    for e in upstream_sorted:
        if len(selected) >= max_words:
            break
        if _take(e):
            stats.upstream_selected += 1
        else:
            stats.upstream_skipped_dup += 1

    stats.total = len(selected)
    return selected, stats


def write_word_pack(
    out_dir: Path,
    entries: list[WordEntry],
    *,
    allow_lines: list[str],
    upstream_commit: str,
    dry_run: bool = False,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    deny_lines = [f"{e.word}\t{e.category}" for e in entries]
    metadata = {
        "upstream": UPSTREAM_REPO,
        "upstream_commit": upstream_commit,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
        "word_count": len(entries),
    }
    if dry_run:
        return

    (out_dir / "deny.tsv").write_text("\n".join(deny_lines) + ("\n" if deny_lines else ""), encoding="utf-8")
    allow_text = "\n".join(t.strip() for t in allow_lines if t.strip())
    if allow_text:
        allow_text += "\n"
    (out_dir / "allow.txt").write_text(allow_text, encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def copy_license(license_src: Path, out_dir: Path, *, dry_run: bool = False) -> None:
    if not license_src.is_file() or dry_run:
        return
    shutil.copy2(license_src, out_dir / "LICENSE")


def read_text_file(path: Path) -> list[str]:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace").splitlines()


def fetch_upstream_text(url: str, timeout: float = 60.0) -> str:
    import requests

    headers = {"User-Agent": "AgentA-sensitive-word-cli"}
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(
                url, timeout=timeout, headers=headers, proxies=current_http_proxy()
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as exc:  # noqa: BLE001 — 重试后统一抛
            last_err = exc
    hint = ""
    if current_http_proxy():
        hint = f"（当前代理端点 {proxy_endpoint_hint(current_http_proxy()['https'])}）"
    raise RuntimeError(f"拉取失败 {url}{hint}: {last_err}") from last_err


def fetch_upstream_commit(timeout: float = 30.0) -> str:
    import requests

    api = f"https://api.github.com/repos/{UPSTREAM_REPO}/commits/{UPSTREAM_BRANCH}"
    headers = {"User-Agent": "AgentA-sensitive-word-cli"}
    resp = requests.get(
        api, timeout=timeout, headers=headers, proxies=current_http_proxy()
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("sha", "unknown"))[:12]


def _upstream_raw_url(rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{UPSTREAM_BRANCH}/{rel_path}"


def resolve_upstream_file(name: str, source_dir: Path | None) -> tuple[Path | None, list[str]]:
    """返回 (本地路径, 文本行)。source_dir 优先，否则返回 (None, []) 表示需联网拉取。"""
    if source_dir is None:
        return None, []
    rel = UPSTREAM_PATHS[name]
    path = (source_dir / rel).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"本地源缺少文件：{path}")
    return path, read_text_file(path)


def download_upstream(source_dir: Path | None) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    for key, rel in UPSTREAM_PATHS.items():
        local_path, lines = resolve_upstream_file(key, source_dir)
        if local_path is not None:
            data[key] = lines
            continue
        text = fetch_upstream_text(_upstream_raw_url(rel))
        data[key] = text.splitlines()
    return data


def build_word_pack(
    out_dir: Path,
    *,
    source_dir: Path | None = None,
    extra_path: Path | None = None,
    max_words: int = 5000,
    include_tags: set[str] | None = None,
    include_dict_orphans: bool = False,
    dry_run: bool = False,
    proxy: str | None = None,
) -> BuildStats:
    """拉取/读取上游词库，裁剪后写入 out_dir。"""
    configure_http_proxy(proxy)
    upstream_commit = "local"
    if source_dir is None:
        upstream_commit = fetch_upstream_commit()

    raw = download_upstream(source_dir)
    upstream = parse_tags_lines(raw["tags"], include_tags=include_tags)

    if include_dict_orphans:
        tagged_keys = {_normalize_key(e.word) for e in upstream}
        for e in parse_dict_lines(raw["dict"]):
            key = _normalize_key(e.word)
            if key and key not in tagged_keys:
                upstream.append(e)

    extras: list[WordEntry] = []
    if extra_path and extra_path.is_file():
        extras = parse_extra_tsv(extra_path)

    selected, stats = select_entries(upstream, extras=extras, max_words=max_words)
    write_word_pack(
        out_dir,
        selected,
        allow_lines=raw["allow"],
        upstream_commit=upstream_commit,
        dry_run=dry_run,
    )

    license_path = source_dir / UPSTREAM_PATHS["license"] if source_dir else None
    if license_path and license_path.is_file():
        copy_license(license_path, out_dir, dry_run=dry_run)
    elif not dry_run and source_dir is None:
        lic_text = fetch_upstream_text(_upstream_raw_url(UPSTREAM_PATHS["license"]))
        (out_dir / "LICENSE").write_text(lic_text, encoding="utf-8")

    return stats


def load_current_stats(word_dir: Path) -> dict[str, object]:
    deny = word_dir / "deny.tsv"
    meta = word_dir / "metadata.json"
    info: dict[str, object] = {
        "dir": str(word_dir),
        "deny_exists": deny.is_file(),
        "word_count": 0,
        "metadata": {},
    }
    if deny.is_file():
        info["word_count"] = sum(
            1 for line in deny.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if meta.is_file():
        info["metadata"] = json.loads(meta.read_text(encoding="utf-8"))
    return info
