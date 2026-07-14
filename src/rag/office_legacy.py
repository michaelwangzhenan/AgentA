"""旧版 Office 格式（.doc / .ppt / .xls）解析。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"


class LegacyOfficeParseError(ValueError):
    """旧版 Office 文件解析失败。"""


# 兼容旧名
LegacyDocParseError = LegacyOfficeParseError


def sniff_office_container(path: Path) -> str:
    """根据文件头判断容器格式：zip（OOXML）或 ole（旧版二进制）。"""
    with path.open("rb") as handle:
        header = handle.read(8)
    if header.startswith(_ZIP_MAGIC):
        return "zip"
    if header[:8] == _OLE_MAGIC:
        return "ole"
    return "unknown"


def sniff_office_word_kind(path: Path) -> str:
    """兼容旧调用：Word 专用嗅探。"""
    kind = sniff_office_container(path)
    if kind == "zip":
        return "docx"
    if kind == "ole":
        return "ole-doc"
    return "unknown"


def _timeout_sec() -> int:
    from src.config import DOCX_PARSE_TIMEOUT_SEC

    return DOCX_PARSE_TIMEOUT_SEC


def _find_antiword() -> str | None:
    return shutil.which("antiword")


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for env_key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env_key)
            if not base:
                continue
            candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def _run_libreoffice_convert(
    path: Path,
    binary: str,
    convert_to: str,
    outdir: Path,
    timeout: int,
) -> bool:
    try:
        completed = subprocess.run(
            [
                binary,
                "--headless",
                "--norestore",
                "--convert-to",
                convert_to,
                "--outdir",
                str(outdir),
                str(path),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LegacyOfficeParseError(
            f"{path.suffix} 解析超时（超过 {timeout} 秒）: {path.name}"
        ) from exc
    except OSError as exc:
        logger.warning("[parser] LibreOffice 启动失败: %s", exc)
        return False

    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        logger.warning(
            "[parser] LibreOffice 转换失败: %s code=%s %s",
            path.name,
            completed.returncode,
            detail[-200:],
        )
        return False
    return True


def _parse_with_antiword(path: Path, binary: str, timeout: int) -> str | None:
    try:
        completed = subprocess.run(
            [binary, "-w", "0", str(path)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LegacyOfficeParseError(
            f".doc 解析超时（超过 {timeout} 秒）: {path.name}"
        ) from exc
    except OSError as exc:
        logger.warning("[parser] antiword 启动失败: %s", exc)
        return None

    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        logger.warning(
            "[parser] antiword 解析失败: %s code=%s %s",
            path.name,
            completed.returncode,
            detail[-200:],
        )
        return None

    text = completed.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    logger.info("[parser] .doc 解析使用 antiword: %s", path.name)
    return text


def _parse_with_libreoffice_txt(path: Path, binary: str, timeout: int) -> str | None:
    try:
        with tempfile.TemporaryDirectory(prefix="agenta-office-") as tmp:
            outdir = Path(tmp)
            if not _run_libreoffice_convert(path, binary, "txt:Text", outdir, timeout):
                return None
            output = outdir / f"{path.stem}.txt"
            if not output.is_file():
                logger.warning("[parser] LibreOffice 未产出 txt: %s", path.name)
                return None
            text = output.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                return None
            logger.info("[parser] .doc 解析使用 LibreOffice: %s", path.name)
            return text
    except LegacyOfficeParseError:
        raise
    except OSError as exc:
        logger.warning("[parser] LibreOffice 启动失败: %s", exc)
        return None


def _require_soffice() -> str:
    soffice = _find_soffice()
    if not soffice:
        raise LegacyOfficeParseError(
            "解析旧版 Office 文件需要 LibreOffice（soffice）。"
            "Ubuntu: sudo apt install -y libreoffice-writer"
        )
    return soffice


def parse_legacy_doc(path: Path) -> str:
    """解析 OLE 格式的 .doc 文件为纯文本。"""
    timeout = _timeout_sec()
    antiword = _find_antiword()
    if antiword:
        text = _parse_with_antiword(path, antiword, timeout)
        if text:
            return text

    soffice = _find_soffice()
    if soffice:
        text = _parse_with_libreoffice_txt(path, soffice, timeout)
        if text:
            return text

    if not antiword and not soffice:
        raise LegacyOfficeParseError(
            "解析 .doc 需要系统安装 antiword 或 LibreOffice（soffice）。"
            "Ubuntu: sudo apt install -y antiword"
        )
    raise LegacyOfficeParseError(f"无法解析 .doc 文件: {path.name}")


def parse_legacy_ppt(path: Path) -> str:
    """解析 OLE 格式的 .ppt：LibreOffice 转 pptx 后走现有解析器。"""
    timeout = _timeout_sec()
    soffice = _require_soffice()
    with tempfile.TemporaryDirectory(prefix="agenta-ppt-") as tmp:
        outdir = Path(tmp)
        if not _run_libreoffice_convert(path, soffice, "pptx", outdir, timeout):
            raise LegacyOfficeParseError(f"无法解析 .ppt 文件: {path.name}")
        output = outdir / f"{path.stem}.pptx"
        if not output.is_file():
            raise LegacyOfficeParseError(f"LibreOffice 未产出 pptx: {path.name}")
        from src.rag.parser import _parse_pptx

        logger.info("[parser] .ppt 解析使用 LibreOffice→pptx: %s", path.name)
        return _parse_pptx(output)


def parse_legacy_xls(path: Path) -> str:
    """解析 OLE 格式的 .xls：LibreOffice 转 xlsx 后走现有解析器。"""
    timeout = _timeout_sec()
    soffice = _require_soffice()
    with tempfile.TemporaryDirectory(prefix="agenta-xls-") as tmp:
        outdir = Path(tmp)
        if not _run_libreoffice_convert(path, soffice, "xlsx", outdir, timeout):
            raise LegacyOfficeParseError(f"无法解析 .xls 文件: {path.name}")
        output = outdir / f"{path.stem}.xlsx"
        if not output.is_file():
            raise LegacyOfficeParseError(f"LibreOffice 未产出 xlsx: {path.name}")
        from src.rag.parser import _parse_xlsx

        logger.info("[parser] .xls 解析使用 LibreOffice→xlsx: %s", path.name)
        return _parse_xlsx(output)
