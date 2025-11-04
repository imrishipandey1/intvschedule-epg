#!/usr/bin/env python3
"""
epg_importer_filtered_v2.py
- Filtered, prioritized EPG importer with threaded image compression.
- Places outputs in indian_channels/ and uk_channels/, assets/...
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from io import BytesIO
import gzip
import json
import os
import re
import requests
import xml.etree.ElementTree as ET
from PIL import Image

# -------------- CONFIG --------------
IST_OFFSET = timedelta(hours=5, minutes=30)
ASSETS_DIR = "assets"
LOGOS_DIR = os.path.join(ASSETS_DIR, "logos")
SHOWS_DIR = os.path.join(ASSETS_DIR, "shows")
MAX_SIZE_KB = 10
MAX_WORKERS = 8  # threads for images
# EPG sources
JIO_URL = "https://avkb.short.gy/jioepg.xml.gz"
TATA_URL = "https://avkb.short.gy/tsepg.xml.gz"
UK_URL = "https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml"
# Filter files
FILTER_IN = "filter.txt"
FILTER_UK = "filter_uk.txt"
# Output roots
INDIAN_ROOT = "indian_channels"
UK_ROOT = "uk_channels"
# ------------------------------------

def ensure_dirs():
    os.makedirs(LOGOS_DIR, exist_ok=True)
    os.makedirs(SHOWS_DIR, exist_ok=True)
    os.makedirs(os.path.join(INDIAN_ROOT, "today"), exist_ok=True)
    os.makedirs(os.path.join(INDIAN_ROOT, "tomorrow"), exist_ok=True)
    os.makedirs(os.path.join(UK_ROOT, "today"), exist_ok=True)
    os.makedirs(os.path.join(UK_ROOT, "tomorrow"), exist_ok=True)

def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    name = name.strip().lower().replace(' ', '-')
    # ensure .json for outputs when used
    return name

def read_filter(path):
    """
    Read filter file with optional rename: "Channel Name=output-file.json"
    Returns dict: { normalized_channel_name_lower : output_filename }
    If file missing or empty -> return {}
    """
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if '=' in line:
                left, right = line.split('=', 1)
                left = left.strip()
                right = right.strip()
                if right == '':
                    out[left.lower()] = sanitize_filename(left) + ".json"
                else:
                    out[left.lower()] = right
            else:
                out[line.lower()] = sanitize_filename(line) + ".json"
    return out

def download_url(url, is_gz=False, timeout=60):
    """Return text content (decompressed if gz)."""
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        if is_gz:
            with gzip.GzipFile(fileobj=BytesIO(r.content)) as gz:
                return gz.read().decode('utf-8')
        return r.text
    except Exception as e:
        print(f"⚠️ Failed download {url}: {e}")
        return None

def iterparse_epg(xml_text):
    """
    Stream-parse XMLTV content and return:
      channels: {channel_id: {'name':display-name, 'logo':icon_src}}
      programmes: {channel_id: [ {title, start_dt, end_dt, icon} ... ]}
    """
    channels = {}
    programmes = {}
    try:
        # iterparse expects a file-like object; use BytesIO of encoded text
        it = ET.iterparse(BytesIO(xml_text.encode('utf-8')), events=('end',))
    except Exception as e:
        print("⚠️ iterparse init error:", e)
        return channels, programmes

    for event, elem in it:
        tag = elem.tag
        if tag == 'channel':
            cid = elem.get('id')
            name = (elem.findtext('display-name') or cid or '').strip()
            icon_el = elem.find('icon')
            logo = icon_el.get('src') if icon_el is not None else ''
            channels[cid] = {'name': name, 'logo': logo}
            elem.clear()
        elif tag == 'programme':
            cid = elem.get('channel')
            if not cid:
                elem.clear()
                continue
            title = (elem.findtext('title') or 'Unknown Show').strip()
            icon_el = elem.find('icon')
            icon = icon_el.get('src') if icon_el is not None else ''
            try:
                start = parse_xmltv_time(elem.get('start'))
                stop = parse_xmltv_time(elem.get('stop'))
            except Exception:
                elem.clear()
                continue
            programmes.setdefault(cid, []).append({
                'title': title,
                'start': start,
                'stop': stop,
                'icon': icon
            })
            elem.clear()
    return channels, programmes

def parse_xmltv_time(time_str):
    # format like YYYYmmddHHMMSS +ZZZZ maybe — split at space if present
    dt_str = time_str.split(' ')[0]
    dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S')
    # EPGs often in local or UTC; we'll not auto-convert here — we'll add IST when formatting
    return dt

def dt_to_ist(dt):
    return dt + IST_OFFSET

def format_time(dt):
    return dt_to_ist(dt).strftime('%I:%M %p').lstrip('0')

def format_date(dt):
    return dt_to_ist(dt).strftime('%B %d, %Y')

def filter_programmes_for_date(progs, target_date):
    """Keep programmes that start on target_date, or cross midnight into target_date."""
    midnight = datetime.combine(target_date, datetime.min.time())
    result = []
    for p in progs:
        s = p['start']
        e = p['stop']
        if s.date() == target_date:
            result.append(p)
        elif s.date() == (target_date - timedelta(days=1)) and e > midnight:
            copy = p.copy()
            copy['start'] = midnight
            result.append(copy)
    result.sort(key=lambda x: x['start'])
    return result

# ---- Image functions ----
def compress_image_webp_from_bytes(img_bytes, max_kb=MAX_SIZE_KB):
    img = Image.open(BytesIO(img_bytes)).convert('RGBA')
    quality = 80
    # If image very big, downscale first to help compression
    # Scale down if raw size > 200KB
    raw_kb = len(img_bytes) / 1024
    if raw_kb > 200:
        # scale by sqrt factor proportionally
        factor = (200 / raw_kb) ** 0.5
        w, h = img.size
        img = img.resize((max(1, int(w * factor)), max(1, int(h * factor))), Image.LANCZOS)

    for q in range(quality, 15, -5):
        buf = BytesIO()
        try:
            img.save(buf, format='WEBP', quality=q, method=6)
        except Exception:
            img = img.convert('RGB')
            img.save(buf, format='WEBP', quality=q, method=6)
        size_kb = len(buf.getvalue()) / 1024
        if size_kb <= max_kb or q <= 20:
            return buf.getvalue()
    # fallback: return last attempt
    return buf.getvalue()

def download_and_save_image(url, save_path):
    """Download & compress into webp under MAX_SIZE_KB. Reuse existing file if exists."""
    try:
        if not url:
            return ""
        # ensure extension and filename
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            return save_path  # reuse
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        webp_bytes = compress_image_webp_from_bytes(r.content)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'wb') as f:
            f.write(webp_bytes)
        return save_path
    except Exception as e:
        # print once; but don't break flow
        print(f"⚠️ image download/compress failed for {url} -> {e}")
        return ""

# ---- Merge logic: prioritise primary dict then fallback ----
def build_channel_output(channel_name_key, desired_filename, primary_map, fallback_map):
    """
    channel_name_key: lowercase channel name from filters
    primary_map: channels dict from primary EPG (channel_id->info)
    fallback_map: channels dict from fallback EPG
    Returns tuple (chosen_channel_id, chosen_channel_info, chosen_programmes_list)
    """
    # find a channel id in primary by name (case-insensitive)
    for cid, info in primary_map.get('channels', {}).items():
        if info['name'].strip().lower() == channel_name_key:
            return cid, info, primary_map.get('programmes', {}).get(cid, [])
    # else try fallback
    for cid, info in fallback_map.get('channels', {}).items():
        if info['name'].strip().lower() == channel_name_key:
            return cid, info, fallback_map.get('programmes', {}).get(cid, [])
    # not found
    return None, None, []

def prepare_epg_maps(xml_text):
    if not xml_text:
        return {'channels':{}, 'programmes':{}}
    ch, pr = iterparse_epg(xml_text)
    return {'channels': ch, 'programmes': pr}

def save_json_schedule(output_path, channel_name, channel_logo_path, date_obj, programmes):
    out = {
        "channel_name": channel_name,
        "channel_logo": channel_logo_path or "",
        "date": format_date(date_obj),
        "schedule": []
    }
    for p in programmes:
        out["schedule"].append({
            "show_name": p['title'],
            "start_time": format_time(p['start']),
            "end_time": format_time(p['stop']),
            "show_logo": p.get('_local_show_logo', "") or ""
        })
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

def process_indian_filters(filters_map):
    """
    filters_map: dict { 'star plus' : 'star-plus.json', ... }
    """
    if not filters_map:
        print("ℹ️ No Indian filters found — skipping Indian EPG.")
        return

    print("⬇️ Downloading prioritized EPGs (Jio primary, Tata fallback)...")
    jio_text = download_url(JIO_URL, is_gz=True)
    tata_text = download_url(TATA_URL, is_gz=True)

    jio_map = prepare_epg_maps(jio_text)
    tata_map = prepare_epg_maps(tata_text)

    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)

    # Collect image tasks
    image_tasks = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as img_exec:
        futures = []

        for name_key, out_filename in filters_map.items():
            cid, info, progs = build_channel_output(name_key, out_filename, jio_map, tata_map)
            if not info:
                print(f"⚠️ Channel '{name_key}' not found in either EPGs — skipping.")
                continue

            # channel logo: prefer info['logo'] from primary (build_channel_output already picked)
            chan_logo_url = info.get('logo', '') or ''
            chan_logo_basename = sanitize_filename(info['name']) + ".webp"
            chan_logo_local = os.path.join(LOGOS_DIR, chan_logo_basename) if chan_logo_url else ""
            if chan_logo_url:
                futures.append(img_exec.submit(download_and_save_image, chan_logo_url, chan_logo_local))

            # For each programme, set local filename tasks
            for p in progs:
                show_logo_url = p.get('icon') or ''
                if show_logo_url:
                    show_basename = sanitize_filename(p['title']) + ".webp"
                    show_local = os.path.join(SHOWS_DIR, show_basename)
                    # Attach local path placeholder to programme dictionary to be filled later
                    p['_local_show_logo'] = show_local
                    futures.append(img_exec.submit(download_and_save_image, show_logo_url, show_local))
                else:
                    p['_local_show_logo'] = ""

            # After scheduling image downloads, we write today/tomorrow jsons (programmes will already have _local_show_logo path strings)
            # Filter programmes for today & tomorrow
            today_progs = filter_programmes_for_date(progs, today)
            tomorrow_progs = filter_programmes_for_date(progs, tomorrow)

            # build output file paths
            output_today = os.path.join(INDIAN_ROOT, "today", out_filename)
            output_tomorrow = os.path.join(INDIAN_ROOT, "tomorrow", out_filename)

            # Save JSONs (show logos will point to local assets path; when Netlify serves, the path is /assets/...)
            # channel logo local path we use the same path if available
            chan_logo_local_rel = chan_logo_local.replace("\\", "/") if chan_logo_local else (info.get('logo') or "")

            save_json_schedule(output_today, info['name'], chan_logo_local_rel, today, today_progs)
            save_json_schedule(output_tomorrow, info['name'], chan_logo_local_rel, tomorrow, tomorrow_progs)

        # Wait for all image futures to finish
        if futures:
            print("⚙️ Compressing and saving images (threaded)...")
            for f in as_completed(futures):
                try:
                    _ = f.result()
                except Exception:
                    pass
    print("✅ Indian EPG filtered processing complete.")

def process_uk_filters(filters_map):
    if not filters_map:
        print("ℹ️ No UK filters found — skipping UK EPG.")
        return

    print("⬇️ Downloading UK EPG...")
    uk_text = download_url(UK_URL, is_gz=False)
    uk_map = prepare_epg_maps(uk_text)

    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as img_exec:
        futures = []
        for name_key, out_filename in filters_map.items():
            # find channel in UK map
            found_cid = None
            found_info = None
            for cid, info in uk_map['channels'].items():
                if info['name'].strip().lower() == name_key:
                    found_cid = cid
                    found_info = info
                    break
            if not found_info:
                print(f"⚠️ UK Channel '{name_key}' not found — skipping.")
                continue

            progs = uk_map['programmes'].get(found_cid, [])
            # schedule channel logo
            chan_logo_url = found_info.get('logo', '')
            if chan_logo_url:
                chan_logo_local = os.path.join(LOGOS_DIR, sanitize_filename(found_info['name']) + ".webp")
                futures.append(img_exec.submit(download_and_save_image, chan_logo_url, chan_logo_local))
                chan_logo_local_rel = chan_logo_local.replace("\\", "/")
            else:
                chan_logo_local_rel = ""

            # schedule show logos and set placeholders
            for p in progs:
                show_logo_url = p.get('icon') or ''
                if show_logo_url:
                    show_local = os.path.join(SHOWS_DIR, sanitize_filename(p['title']) + ".webp")
                    p['_local_show_logo'] = show_local
                    futures.append(img_exec.submit(download_and_save_image, show_logo_url, show_local))
                else:
                    p['_local_show_logo'] = ""

            # write JSONs
            today_progs = filter_programmes_for_date(progs, today)
            tomorrow_progs = filter_programmes_for_date(progs, tomorrow)
            save_json_schedule(os.path.join(UK_ROOT, "today", out_filename), found_info['name'], chan_logo_local_rel, today, today_progs)
            save_json_schedule(os.path.join(UK_ROOT, "tomorrow", out_filename), found_info['name'], chan_logo_local_rel, tomorrow, tomorrow_progs)

        if futures:
            print("⚙️ Compressing and saving UK images (threaded)...")
            for f in as_completed(futures):
                try:
                    _ = f.result()
                except Exception:
                    pass
    print("✅ UK EPG filtered processing complete.")

def main():
    ensure_dirs()
    filters_ind = read_filter(FILTER_IN)
    filters_uk = read_filter(FILTER_UK)

    # If both empty, nothing to do.
    if not filters_ind and not filters_uk:
        print("ℹ️ Both filter files empty or missing. Nothing to do.")
        return

    # Process Indian (with priority)
    if filters_ind:
        process_indian_filters(filters_ind)
    else:
        print("ℹ️ Skipping Indian EPG (no filters defined).")

    # Process UK
    if filters_uk:
        process_uk_filters(filters_uk)
    else:
        print("ℹ️ Skipping UK EPG (no filters defined).")

    print("🎯 All done.")
    return

if __name__ == "__main__":
    main()
