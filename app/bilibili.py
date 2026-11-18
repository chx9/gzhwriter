"""B 站字幕提取：yt-dlp 优先（ players 已提供的字幕轨道，含 AI 字幕），
Bilibili 公开 API 兜底。提取结果转成 SRT 文本，走口语稿入库管线（Sxx + [¶n @mm:ss]）。

依赖 yt-dlp（pip 包）；未安装时自动降级到直连 API（覆盖面较小）。
部分视频的字幕需要登录态：设置环境变量 GZH_BILIBILI_BROWSER=chrome / safari / edge 等，
yt-dlp 会从本机浏览器读取会话（不导出 Cookie）。
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import httpx

from .errors import ApiError

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
PREFERRED_LANGS = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "ai-zh", "en"]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _bvid_from_url(url: str) -> str | None:
    match = BV_RE.search(url or "")
    return match.group(1) if match else None


def _srt_time(seconds: float) -> str:
    total = max(0.0, float(seconds or 0))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")


def _srt_from_bilibili_json(data: dict[str, Any]) -> str:
    body = data.get("body") or []
    lines: list[str] = []
    for index, item in enumerate(body, 1):
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{index}\n{_srt_time(item.get('from', 0))} --> {_srt_time(item.get('to', 0))}\n{content}")
    if not lines:
        raise ApiError("字幕文件里没有内容", "NO_SUBTITLE")
    return "\n\n".join(lines) + "\n"


def _pick_subtitle(subtitles: list[dict[str, Any]]) -> dict[str, Any]:
    for lang in PREFERRED_LANGS:
        for item in subtitles:
            if item.get("lan") == lang:
                return item
    return subtitles[0]


def fetch_via_api(url: str, *, timeout: float = 25.0) -> dict[str, str]:
    """不依赖 yt-dlp 的直连方案：view → player/v2 → 字幕 json。"""
    headers = {"user-agent": UA, "referer": "https://www.bilibili.com/"}
    bvid = _bvid_from_url(url)
    if not bvid and "b23.tv" in (url or ""):
        try:
            response = httpx.get(url, headers=headers, follow_redirects=True, timeout=timeout)
            bvid = _bvid_from_url(str(response.url))
        except httpx.HTTPError:
            bvid = None
    if not bvid:
        raise ApiError("从链接里解析不出 BV 号，请使用完整的 B 站视频链接", "BAD_URL")

    try:
        view = httpx.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
            headers=headers,
            timeout=timeout,
        )
        data = view.json()
        if data.get("code") != 0:
            raise ApiError(f"获取视频信息失败：{data.get('message') or data.get('code')}", "FETCH_FAILED")
        cid = data["data"]["cid"]
        title = str(data["data"].get("title") or bvid)
    except httpx.HTTPError as exc:
        raise ApiError(f"连不上 Bilibili：{exc}", "FETCH_FAILED") from exc

    try:
        player = httpx.get(
            "https://api.bilibili.com/x/player/v2",
            params={"bvid": bvid, "cid": cid},
            headers=headers,
            timeout=timeout,
        )
        subtitles = (player.json().get("data") or {}).get("subtitle", {}).get("subtitles") or []
    except httpx.HTTPError as exc:
        raise ApiError(f"获取字幕列表失败：{exc}", "FETCH_FAILED") from exc
    if not subtitles:
        raise ApiError(
            "这个视频没有公开字幕轨道（可能需要登录 Cookie；装好 yt-dlp 并设置 GZH_BILIBILI_BROWSER=chrome 再试）",
            "NO_SUBTITLE",
        )

    chosen = _pick_subtitle(subtitles)
    sub_url = str(chosen.get("subtitle_url") or "")
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    try:
        sub_response = httpx.get(sub_url, headers=headers, timeout=timeout)
        srt = _srt_from_bilibili_json(sub_response.json())
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise ApiError(f"下载字幕失败：{exc}", "FETCH_FAILED") from exc
    return {"title": title, "srt": srt}


def fetch_via_ytdlp(
    url: str,
    *,
    cookies_browser: str | None = None,
    cookie_file: str | None = None,
    timeout: float = 30.0,
) -> dict[str, str]:
    from yt_dlp import YoutubeDL  # 延迟导入：没装也不影响启动

    with tempfile.TemporaryDirectory(prefix="gzh-bili-") as tmp:
        options: dict[str, Any] = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": PREFERRED_LANGS,
            "subtitlesformat": "srt/vtt",
            "outtmpl": str(Path(tmp) / "sub"),
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": timeout,
            "retries": 1,
            "noplaylist": True,
        }
        if cookie_file:
            options["cookiefile"] = cookie_file
        elif cookies_browser:
            options["cookiesfrombrowser"] = (cookies_browser,)
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
            raise ApiError(f"yt-dlp 提取失败：{exc}", "FETCH_FAILED") from exc
        files = sorted(Path(tmp).glob("sub.*"))
        if not files:
            raise ApiError(
                "没抓到字幕轨道：这个视频可能没有字幕，或需要登录。"
                "确认播放器里能选到字幕；仍不行就设置 GZH_BILIBILI_BROWSER=chrome（或其他浏览器名）后重试",
                "NO_SUBTITLE",
            )
        title = str((info or {}).get("title") or "B站视频")
        return {"title": title, "srt": files[0].read_text("utf8", "replace")}


def fetch_bilibili_subtitles(
    url: str,
    *,
    cookies_browser: str | None = None,
    cookie_file: str | None = None,
) -> dict[str, str]:
    """提取 B 站视频字幕 → {title, srt}。

    优先级：yt-dlp（支持浏览器 Cookie / cookies.txt）→ 直连公开 API。
    两边都拿不到时，给出一条包含登录说明的可操作错误。
    """
    if "bilibili.com" not in url and "b23.tv" not in url:
        raise ApiError("这不是 B 站链接（支持 bilibili.com 和 b23.tv）", "BAD_URL")
    ytdlp_error: ApiError | None = None
    try:
        import yt_dlp  # noqa: F401

        return fetch_via_ytdlp(url, cookies_browser=cookies_browser, cookie_file=cookie_file)
    except ImportError:
        pass
    except ApiError as exc:
        if exc.code == "BAD_URL":
            raise
        ytdlp_error = exc

    try:
        return fetch_via_api(url)
    except ApiError as exc:
        if ytdlp_error is not None and exc.code in {"NO_SUBTITLE", "FETCH_FAILED"}:
            raise ApiError(
                "没拿到字幕。B 站的字幕轨道（含 AI 字幕）通常需要登录才能读取：\n"
                "1) 在浏览器里登录 B 站，确认播放器里能选到字幕；\n"
                "2) 设置 GZH_BILIBILI_BROWSER=chrome（或 safari / edge），"
                "且读取 Chrome Cookie 时最好先退出 Chrome；\n"
                "3) 或者导出 cookies.txt，设置 GZH_BILIBILI_COOKIE_FILE=/path/cookies.txt。\n"
                f"（yt-dlp：{ytdlp_error.message[:160]}；直连：{exc.message[:120]}）",
                "NO_SUBTITLE",
            ) from exc
        raise
