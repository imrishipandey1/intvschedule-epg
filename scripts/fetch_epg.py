#!/usr/bin/env python3
"""
Fetches Indian TV EPG from Jio (priority) and Tata Play (fallback),
filters by channel names from filter.txt, and writes 24h schedules
for Today and Tomorrow (IST) to output-today/ and output-tomorrow/.

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
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple, Iterable, Optional, Set

JIO_URL = "https://avkb.short.gy/jioepg.xml.gz"
TATA_URL = "https://avkb.short.gy/tsepg.xml.gz"
IST = ZoneInfo("Asia/Kolkata")

# ---------- Utilities ----------

def slugify(name: str) -> str:
    # lowercase, replace spaces/&/punctuation with hyphens, squeeze dashes
    s = name.lower()
    s = re.sub(r"[&]", " and ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s if s else "channel"

def normalize_name(name: str) -> str:
    """
    Normalize for matching:
    - lowercase
    - & -> and
    - remove spaces, hyphens, underscores, dots
    - collapse 'plus' variants (e.g., 'starplus' vs 'star plus')
    - remove 'hd' suffix if present to get base form (but we also keep hd variant)
    """
    s = name.lower()
    s = s.replace("&", "and")
    s = re.sub(r"[\s\-\_\.]", "", s)
    return s

def base_name_no_hd(norm: str) -> str:
    # remove trailing 'hd' if present
    return re.sub(r"hd$", "", norm)

def fetch_gz(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()

def iter_xml(path_or_bytes: bytes | str) -> Iterable[ET.Element]:
    """
    Efficient streaming iterator over top-level elements in a large XMLTV file.
    Yields completed <channel> and <programme> elements.
    """
    if isinstance(path_or_bytes, bytes):
        raw = io.BytesIO(path_or_bytes)
    else:
        raw = open(path_or_bytes, "rb")
    with raw:
        with gzip.GzipFile(fileobj=raw, mode="rb") as gz:
            # iterparse needs a file-like; wrap gz directly
            context = ET.iterparse(gz, events=("start", "end"))
            _, root = next(context)  # get root <tv>
            for event, elem in context:
                if event == "end" and elem.tag in ("channel", "programme"):
                    yield elem
                    root.clear()  # free memory

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

# ---------- Parsing ----------

def parse_channels(xml_bytes: bytes) -> Tuple[Dict[str, ChannelMeta], Dict[str, List[str]]]:
    """
    Returns:
      by_id: channel_id -> ChannelMeta
      name_index: normalized_name -> [channel_ids]
    """
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
            # Example: "20251102183000 +0000"
            start_dt = datetime.strptime(start_raw, "%Y%m%d%H%M%S %z")
            end_dt = datetime.strptime(stop_raw,  "%Y%m%d%H%M%S %z")
        except Exception:
            # try without space before %z just in case
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

    # sort by UTC start to keep stable order before local conversion
    for cid in list(out.keys()):
        out[cid].sort(key=lambda p: p.start_utc)
    return out

# ---------- Matching logic ----------

def choose_channel_for_target(
    target_name: str,
    jio_by_id: Dict[str, ChannelMeta], jio_idx: Dict[str, List[str]],
    tata_by_id: Dict[str, ChannelMeta], tata_idx: Dict[str, List[str]],
) -> Tuple[str, Optional[ChannelMeta], str]:
    """
    Returns (chosen_source, chosen_meta_or_None, reason)
    chosen_source in {"jio", "tata", "none"}
    """
    t_norm = normalize_name(target_name)
    t_base = base_name_no_hd(t_norm)
    candidates = [t_norm, t_base, t_base + "hd", t_norm + "hd"]

    # prefer Jio
    for c in candidates:
        ids = jio_idx.get(c)
        if ids:
            cid = ids[0]
            return "jio", jio_by_id.get(cid), "match-jio"

    # if not in Jio, try Tata
    for c in candidates:
        ids = tata_idx.get(c)
        if ids:
            cid = ids[0]
            return "tata", tata_by_id.get(cid), "match-tata"

    return "none", None, "not-found"

def local_day_window_ist(day_offset: int = 0) -> Tuple[datetime, datetime, str]:
    """
    Returns IST-local window [start, end) and the YYYY-MM-DD date string.
    day_offset=0 -> today, 1 -> tomorrow
    """
    now_ist = datetime.now(IST)
    today_ist = now_ist.date()
    target_date = today_ist + timedelta(days=day_offset)

    start_local = datetime.combine(target_date, datetime.min.time(), tzinfo=IST)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local, target_date.isoformat()

def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return (a_start < b_end) and (a_end > b_start)

def format_time_12h(dt_local: datetime) -> str:
    # "12:30 AM" style
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
    # de-duplicate while preserving order (case-insensitive)
    seen = set()
    targets: List[str] = []
    for t in targets_raw:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            targets.append(t)

    print(f"Loaded {len(targets)} channels from filter.txt")

    # Fetch both feeds
    print("Fetching Jio EPG...")
    jio_bytes = fetch_gz(JIO_URL)
    print(f"Fetched {len(jio_bytes):,} bytes (gz)")

    print("Fetching Tata Play EPG...")
    tata_bytes = fetch_gz(TATA_URL)
    print(f"Fetched {len(tata_bytes):,} bytes (gz)")

    # Parse channels first
    print("Parsing channels (Jio)...")
    jio_by_id, jio_idx = parse_channels(jio_bytes)
    print(f"Jio channels: {len(jio_by_id)}")

    print("Parsing channels (Tata)...")
    tata_by_id, tata_idx = parse_channels(tata_bytes)
    print(f"Tata channels: {len(tata_by_id)}")

    # Decide per target which source/channel to use
    selections: Dict[str, Tuple[str, Optional[ChannelMeta]]] = {}  # target_lower -> (source, meta)
    wanted_ids_by_source: Dict[str, Set[str]] = {"jio": set(), "tata": set()}

    for t in targets:
        src, meta, _ = choose_channel_for_target(t, jio_by_id, jio_idx, tata_by_id, tata_idx)
        selections[t.lower()] = (src, meta)
        if src in ("jio", "tata") and meta is not None:
            wanted_ids_by_source[src].add(meta.id)
        print(f"Target '{t}': {src.upper() if src!='none' else 'NONE'} - {meta.name if meta else 'not found'}")

    # Parse programmes only for needed channel ids (one pass per feed)
    print("Parsing programmes (Jio)...")
    jio_prog = parse_programmes_for_ids(jio_bytes, wanted_ids_by_source["jio"])
    print("Parsing programmes (Tata)...")
    tata_prog = parse_programmes_for_ids(tata_bytes, wanted_ids_by_source["tata"])

    # Build IST windows
    today_start, today_end, today_str = local_day_window_ist(0)
    tomorrow_start, tomorrow_end, tomorrow_str = local_day_window_ist(1)

    # Prepare output dirs
    out_today = os.path.join(repo_root, "output-today")
    out_tomorrow = os.path.join(repo_root, "output-tomorrow")
    os.makedirs(out_today, exist_ok=True)
    os.makedirs(out_tomorrow, exist_ok=True)

    # For each target, build JSON for today and tomorrow
    for t in targets:
        key = t.lower()
        src, meta = selections[key]
        if src == "none" or meta is None:
            # write empty files with metadata only
            for day_name, d_start, d_end, d_str, out_dir in [
                ("today", today_start, today_end, today_str, out_today),
                ("tomorrow", tomorrow_start, tomorrow_end, tomorrow_str, out_tomorrow),
            ]:
                data = {
                    "channel_name": t,  # keep original filter name if not found
                    "channel_logo": "",
                    "date": d_str,
                    "programs": [],
                }
                filename = os.path.join(out_dir, f"{slugify(t)}.json")
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            continue

        # choose feed lists
        progs_source = jio_prog if src == "jio" else tata_prog
        progs_list = progs_source.get(meta.id, [])

        # If Jio chosen but *no* programmes in window for both days, fall back to Tata for those days only
        # (Based on your spec: if Jio has no schedule for the day, fall back to Tata Play)
        fallback_list = tata_prog.get(meta.id, []) if src == "jio" else []

        # metadata (prefer Jio; else Tata; else empty) — already determined by selection
        channel_name = meta.name or t
        channel_logo = meta.logo or ""

        day_specs = [
            ("today", today_start, today_end, today_str, out_today),
            ("tomorrow", tomorrow_start, tomorrow_end, tomorrow_str, out_tomorrow),
        ]

        for day_name, d_start, d_end, d_str, out_dir in day_specs:
            # choose which programme list to use for this day
            lst = progs_list
            if src == "jio":
                has_any = any(
                    overlaps(p.start_utc.astimezone(IST), p.end_utc.astimezone(IST), d_start, d_end)
                    for p in lst
                )
                if not has_any and fallback_list:
                    lst = fallback_list  # day-level fallback to Tata

            # Filter by overlap with IST window, then map to JSON rows
            day_rows: List[dict] = []
            for p in lst:
                start_local = p.start_utc.astimezone(IST)
                end_local = p.end_utc.astimezone(IST)
                if not overlaps(start_local, end_local, d_start, d_end):
                    continue

                # Clip to window for formatting? You asked for the real start/end display times, not clipped,
                # so keep the programme's local times as-is.
                day_rows.append({
                    "title": p.title or "",
                    "start_time": format_time_12h(start_local),
                    "end_time": format_time_12h(end_local),
                    "show_logo": p.show_logo or "",
                })

            # Sort by start time local (strings already formatted could mis-sort; sort by actual dt)
            day_rows.sort(key=lambda r: datetime.strptime(r["start_time"], "%I:%M %p"))

            data = {
                "channel_name": channel_name,
                "channel_logo": channel_logo,
                "date": d_str,
                "programs": day_rows,
            }

            filename = os.path.join(out_dir, f"{slugify(channel_name)}.json")
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    print("Done.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
