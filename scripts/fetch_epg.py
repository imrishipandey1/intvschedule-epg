#!/usr/bin/env python3
"""
Fetches Indian TV EPG from Jio (priority) and Tata Play (fallback),
filters by channel names from filter.txt, and writes 24h schedules
for Today and Tomorrow (IST) to output-today/ and output-tomorrow/.

NEW: Downloads each programme's logo <icon src="...">, compresses to WebP
under 10 KB, and saves as: assets/<channel-slug>/<show-slug>.webp

Output JSON (per channel per day):
{
  "channel_name": "Sony SAB",
  "channel_logo": "https://...",
  "date": "YYYY-MM-DD",
  "programs": [
    {
      "title": "Show name",
      "start_time": "06:30 PM",
      "end_time": "07:00 PM",
      "show_logo": "" | "https://..."
    }
  ]
}
"""

from __future__ import annotations

import json
import os
import re
import sys
import gzip
import io
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple, Iterable, Optional, Set

# Third-party (standard GitHub runner can install via requirements.txt)
from PIL import Image
from PIL import ImageOps

JIO_URL = "https://avkb.short.gy/jioepg.xml.gz"
TATA_URL = "https://avkb.short.gy/tsepg.xml.gz"
IST = ZoneInfo("Asia/Kolkata")

ASSETS_DIR = "assets"
MAX_BYTES = 10 * 1024  # 10 KB
# Try qualities from high to low; if still too big, auto-resize then retry qualities
QUALITY_STEPS = [85, 70, 60, 50, 40, 30, 20, 10]
# When resizing, scale longest edge down progressively
DOWNSCALE_FACTORS = [1.0, 0.85, 0.75, 0.65, 0.55, 0.45, 0.35, 0.25]

# ---------- Utilities ----------

def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[&]", " and ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s if s else "channel"

def normalize_name(name: str) -> str:
    s = name.lower()
    s = s.replace("&", "and")
    s = re.sub(r"[\s\-\_\.]", "", s)
    return s

def base_name_no_hd(norm: str) -> str:
    return re.sub(r"hd$", "", norm)

def fetch_gz(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()

def http_get_bytes(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None

def iter_xml(path_or_bytes: bytes | str) -> Iterable[ET.Element]:
    if isinstance(path_or_bytes, bytes):
        raw = io.BytesIO(path_or_bytes)
    else:
        raw = open(path_or_bytes, "rb")
    with raw:
        with gzip.GzipFile(fileobj=raw, mode="rb") as gz:
            context = ET.iterparse(gz, events=("start", "end"))
            _, root = next(context)
            for event, elem in context:
                if event == "end" and elem.tag in ("channel", "programme"):
                    yield elem
                    root.clear()

@dataclass
class ChannelMeta:
    id: str
    name: str
    logo: str

@dataclass
class Programme:
    start_utc: datetime
    end_utc: datetime
    title: str
    show_logo: str

# ---------- Image handling ----------

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def load_image_from_bytes(data: bytes) -> Optional[Image.Image]:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return im
    except Exception:
        return None

def save_webp_under_size(img: Image.Image, out_path: str, max_bytes: int = MAX_BYTES) -> bool:
    """
    Save image as WebP under max_bytes if possible by adjusting quality and size.
    Returns True if saved under limit, else False (saves best-effort anyway).
    """
    # Convert to RGB to avoid mode issues (e.g., P/LA) and strip metadata
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    best_data = None
    best_len = float("inf")

    # Try progressive downscales; at each scale, try different qualities
    w0, h0 = img.size
    for scale in DOWNSCALE_FACTORS:
        w = max(1, int(w0 * scale))
        h = max(1, int(h0 * scale))
        if (w, h) != img.size:
            cand = img.resize((w, h), Image.Resampling.LANCZOS)
        else:
            cand = img

        for q in QUALITY_STEPS:
            buf = io.BytesIO()
            try:
                cand.save(
                    buf,
                    format="WEBP",
                    quality=q,
                    method=6,         # better compression
                    exact=True,
                    lossless=False,
                )
            except Exception:
                continue

            data = buf.getvalue()
            size = len(data)

            if size < best_len:
                best_len = size
                best_data = data

            if size <= max_bytes:
                with open(out_path, "wb") as f:
                    f.write(data)
                return True

    # If nothing met the limit, save best-effort smallest
    if best_data is not None:
        with open(out_path, "wb") as f:
            f.write(best_data)
        return False

    return False

def download_and_compress_logo(url: str, channel_slug: str, show_slug: str) -> Optional[str]:
    """
    Downloads image at URL, converts/compresses to WebP under 10KB if possible,
    writes to assets/<channel_slug>/<show_slug>.webp, and returns the saved path.
    """
    if not url:
        return None

    # Directory
    channel_dir = os.path.join(ASSETS_DIR, channel_slug)
    ensure_dir(channel_dir)

    # Deduplicate filename if exists
    base_name = f"{show_slug}.webp"
    out_path = os.path.join(channel_dir, base_name)
    if os.path.exists(out_path):
        # append a numeric suffix to avoid collisions
        i = 2
        while True:
            alt = os.path.join(channel_dir, f"{show_slug}-{i}.webp")
            if not os.path.exists(alt):
                out_path = alt
                break
            i += 1

    # Fetch
    raw = http_get_bytes(url)
    if not raw:
        return None

    # Some server images may already be webp & small; if so, just write
    if raw[:12].lower().startswith(b"riff") and len(raw) <= MAX_BYTES:
        try:
            with open(out_path, "wb") as f:
                f.write(raw)
            return out_path
        except Exception:
            return None

    img = load_image_from_bytes(raw)
    if img is None:
        return None

    try:
        save_webp_under_size(img, out_path, MAX_BYTES)
        return out_path
    except Exception:
        return None

# ---------- Parsing ----------

def parse_channels(xml_bytes: bytes) -> Tuple[Dict[str, ChannelMeta], Dict[str, List[str]]]:
    by_id: Dict[str, ChannelMeta] = {}
    name_index: Dict[str, List[str]] = {}

    for elem in iter_xml(xml_bytes):
        if elem.tag != "channel":
            continue
        cid = elem.get("id", "").strip()
        if not cid:
            continue
        name_el = elem.find("display-name")
        icon_el = elem.find("icon")
        display = (name_el.text or "").strip() if name_el is not None else ""
        logo = icon_el.get("src", "").strip() if icon_el is not None else ""
        meta = ChannelMeta(id=cid, name=display, logo=logo)
        by_id[cid] = meta

        n = normalize_name(display)
        for key in {n, base_name_no_hd(n)}:
            if not key:
                continue
            name_index.setdefault(key, []).append(cid)

        elem.clear()

    return by_id, name_index

def parse_programmes_for_ids(xml_bytes: bytes, wanted_ids: Set[str]) -> Dict[str, List[Programme]]:
    out: Dict[str, List[Programme]] = {cid: [] for cid in wanted_ids}
    if not wanted_ids:
        return out
    for elem in iter_xml(xml_bytes):
        if elem.tag != "programme":
            continue
        cid = elem.get("channel", "").strip()
        if cid not in wanted_ids:
            elem.clear()
            continue

        start_raw = elem.get("start", "")
        stop_raw = elem.get("stop", "")
        try:
            start_dt = datetime.strptime(start_raw, "%Y%m%d%H%M%S %z")
            end_dt = datetime.strptime(stop_raw,  "%Y%m%d%H%M%S %z")
        except Exception:
            try:
                start_dt = datetime.strptime(start_raw, "%Y%m%d%H%M%S%z")
                end_dt = datetime.strptime(stop_raw,  "%Y%m%d%H%M%S%z")
            except Exception:
                elem.clear()
                continue

        title_el = elem.find("title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        show_icon_el = elem.find("icon")
        show_logo = show_icon_el.get("src", "").strip() if show_icon_el is not None else ""

        out.setdefault(cid, []).append(
            Programme(start_utc=start_dt.astimezone(ZoneInfo("UTC")),
                      end_utc=end_dt.astimezone(ZoneInfo("UTC")),
                      title=title,
                      show_logo=show_logo)
        )

        elem.clear()

    for cid in list(out.keys()):
        out[cid].sort(key=lambda p: p.start_utc)
    return out

# ---------- Matching logic ----------

def choose_channel_for_target(
    target_name: str,
    jio_by_id: Dict[str, ChannelMeta], jio_idx: Dict[str, List[str]],
    tata_by_id: Dict[str, ChannelMeta], tata_idx: Dict[str, List[str]],
) -> Tuple[str, Optional[ChannelMeta], str]:
    t_norm = normalize_name(target_name)
    t_base = base_name_no_hd(t_norm)
    candidates = [t_norm, t_base, t_base + "hd", t_norm + "hd"]

    for c in candidates:
        ids = jio_idx.get(c)
        if ids:
            cid = ids[0]
            return "jio", jio_by_id.get(cid), "match-jio"

    for c in candidates:
        ids = tata_idx.get(c)
        if ids:
            cid = ids[0]
            return "tata", tata_by_id.get(cid), "match-tata"

    return "none", None, "not-found"

def local_day_window_ist(day_offset: int = 0) -> Tuple[datetime, datetime, str]:
    now_ist = datetime.now(IST)
    today_ist = now_ist.date()
    target_date = today_ist + timedelta(days=day_offset)
    start_local = datetime.combine(target_date, datetime.min.time(), tzinfo=IST)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local, target_date.isoformat()

def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return (a_start < b_end) and (a_end > b_start)

def format_time_12h(dt_local: datetime) -> str:
    return dt_local.strftime("%I:%M %p").lstrip("0").replace(" 0", " ")

# ---------- Main flow ----------

def main() -> int:
    repo_root = os.getcwd()
    filter_path = os.path.join(repo_root, "filter.txt")
    if not os.path.exists(filter_path):
        print("ERROR: filter.txt not found in repository root.", file=sys.stderr)
        return 1

    with open(filter_path, "r", encoding="utf-8") as f:
        targets_raw = [line.strip() for line in f if line.strip()]
    seen = set()
    targets: List[str] = []
    for t in targets_raw:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            targets.append(t)

    print(f"Loaded {len(targets)} channels from filter.txt")

    print("Fetching Jio EPG...")
    jio_bytes = fetch_gz(JIO_URL)
    print(f"Fetched {len(jio_bytes):,} bytes (gz)")

    print("Fetching Tata Play EPG...")
    tata_bytes = fetch_gz(TATA_URL)
    print(f"Fetched {len(tata_bytes):,} bytes (gz)")

    print("Parsing channels (Jio)...")
    jio_by_id, jio_idx = parse_channels(jio_bytes)
    print(f"Jio channels: {len(jio_by_id)}")

    print("Parsing channels (Tata)...")
    tata_by_id, tata_idx = parse_channels(tata_bytes)
    print(f"Tata channels: {len(tata_by_id)}")

    selections: Dict[str, Tuple[str, Optional[ChannelMeta]]] = {}
    wanted_ids_by_source: Dict[str, Set[str]] = {"jio": set(), "tata": set()}

    for t in targets:
        src, meta, _ = choose_channel_for_target(t, jio_by_id, jio_idx, tata_by_id, tata_idx)
        selections[t.lower()] = (src, meta)
        if src in ("jio", "tata") and meta is not None:
            wanted_ids_by_source[src].add(meta.id)
        print(f"Target '{t}': {src.upper() if src!='none' else 'NONE'} - {meta.name if meta else 'not found'}")

    print("Parsing programmes (Jio)...")
    jio_prog = parse_programmes_for_ids(jio_bytes, wanted_ids_by_source["jio"])
    print("Parsing programmes (Tata)...")
    tata_prog = parse_programmes_for_ids(tata_bytes, wanted_ids_by_source["tata"])

    today_start, today_end, today_str = local_day_window_ist(0)
    tomorrow_start, tomorrow_end, tomorrow_str = local_day_window_ist(1)

    out_today = os.path.join(repo_root, "output-today")
    out_tomorrow = os.path.join(repo_root, "output-tomorrow")
    os.makedirs(out_today, exist_ok=True)
    os.makedirs(out_tomorrow, exist_ok=True)
    os.makedirs(ASSETS_DIR, exist_ok=True)

    # Track downloaded logos to avoid re-downloading same URL within one run
    downloaded_map: Dict[str, str] = {}  # url -> saved path

    for t in targets:
        key = t.lower()
        src, meta = selections[key]
        if src == "none" or meta is None:
            for d_start, d_end, d_str, out_dir in [
                (today_start, today_end, today_str, out_today),
                (tomorrow_start, tomorrow_end, tomorrow_str, out_tomorrow),
            ]:
                data = {
                    "channel_name": t,
                    "channel_logo": "",
                    "date": d_str,
                    "programs": [],
                }
                filename = os.path.join(out_dir, f"{slugify(t)}.json")
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            continue

        progs_source = jio_prog if src == "jio" else tata_prog
        progs_list = progs_source.get(meta.id, [])
        fallback_list = tata_prog.get(meta.id, []) if src == "jio" else []

        channel_name = meta.name or t
        channel_logo = meta.logo or ""
        channel_slug = slugify(channel_name)

        day_specs = [
            (today_start, today_end, today_str, out_today),
            (tomorrow_start, tomorrow_end, tomorrow_str, out_tomorrow),
        ]

        for d_start, d_end, d_str, out_dir in day_specs:
            lst = progs_list
            if src == "jio":
                has_any = any(
                    overlaps(p.start_utc.astimezone(IST), p.end_utc.astimezone(IST), d_start, d_end)
                    for p in lst
                )
                if not has_any and fallback_list:
                    lst = fallback_list

            # Build rows and download logos
            rows: List[dict] = []
            # For per-channel filename de-dup on same slug
            seen_show_filenames: Set[str] = set()

            for p in lst:
                start_local = p.start_utc.astimezone(IST)
                end_local = p.end_utc.astimezone(IST)
                if not overlaps(start_local, end_local, d_start, d_end):
                    continue

                title = p.title or ""
                start_str = format_time_12h(start_local)
                end_str = format_time_12h(end_local)
                show_logo_url = p.show_logo or ""

                # Download & compress once per URL; save under assets/<channel>/<show-slug>.webp
                saved_path = None
                if show_logo_url:
                    if show_logo_url in downloaded_map:
                        saved_path = downloaded_map[show_logo_url]
                    else:
                        # slugify title for filename; ensure uniqueness
                        base_slug = slugify(title) or "show"
                        show_slug = base_slug
                        # Avoid clobbering within the same channel/day
                        channel_dir = os.path.join(ASSETS_DIR, channel_slug)
                        ensure_dir(channel_dir)
                        i = 2
                        while f"{show_slug}.webp" in seen_show_filenames or os.path.exists(os.path.join(channel_dir, f"{show_slug}.webp")):
                            show_slug = f"{base_slug}-{i}"
                            i += 1

                        saved = download_and_compress_logo(show_logo_url, channel_slug, show_slug)
                        if saved:
                            saved_path = saved
                            seen_show_filenames.add(os.path.basename(saved))
                            downloaded_map[show_logo_url] = saved

                rows.append({
                    "title": title,
                    "start_time": start_str,
                    "end_time": end_str,
                    "show_logo": show_logo_url if show_logo_url else "",
                })

            # Sort by true time
            rows.sort(key=lambda r: datetime.strptime(r["start_time"], "%I:%M %p"))

            data = {
                "channel_name": channel_name,
                "channel_logo": channel_logo,
                "date": d_str,
                "programs": rows,
            }

            filename = os.path.join(out_dir, f"{channel_slug}.json")
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    print("Done.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
