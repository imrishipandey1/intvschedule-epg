#!/usr/bin/env python3
import requests
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json
import os
import re
from io import BytesIO
from PIL import Image

# --- SETTINGS ---
IST_OFFSET = timedelta(hours=5, minutes=30)
ASSETS_DIR = "assets"
FILTER_FILE = "filter.txt"
MAX_SIZE_KB = 10
# ----------------

def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    return name.strip().lower().replace(' ', '-')

def parse_xmltv_time(time_str, convert_to_ist=False):
    dt_str = time_str.split(' ')[0]
    dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S')
    if convert_to_ist:
        dt += IST_OFFSET
    return dt

def format_time(dt):
    return dt.strftime('%I:%M %p').lstrip('0')

def format_date(dt):
    return dt.strftime('%B %d, %Y')

def download_epg(url, is_gz=True):
    print(f"⬇️  Downloading: {url}")
    if 'github.com' in url and '/blob/' in url:
        url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    if is_gz:
        with gzip.GzipFile(fileobj=BytesIO(r.content)) as gz:
            return gz.read().decode('utf-8')
    return r.text

def read_filter_list(path):
    if not os.path.exists(path):
        print(f"⚠️ No filter.txt found at {path}, importing all channels.")
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip().lower() for line in f if line.strip()]

def compress_image_to_webp(url, save_path):
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        # Compress loop
        quality = 80
        while True:
            buffer = BytesIO()
            img.save(buffer, format='WEBP', quality=quality, optimize=True)
            size_kb = len(buffer.getvalue()) / 1024
            if size_kb <= MAX_SIZE_KB or quality <= 25:
                break
            quality -= 5

        with open(save_path, 'wb') as f:
            f.write(buffer.getvalue())
        return save_path

    except Exception as e:
        print(f"⚠️  Failed to compress {url}: {e}")
        return None

def parse_epg_xml(xml_content, allowed_channels=None, convert_to_ist=False):
    channels = {}
    programmes = {}

    for event, elem in ET.iterparse(BytesIO(xml_content.encode('utf-8')), events=('end',)):
        if elem.tag == 'channel':
            cid = elem.get('id')
            name = elem.findtext('display-name') or cid
            icon = elem.find('icon')
            logo = icon.get('src') if icon is not None else ''
            channels[cid] = {'name': name, 'logo': logo}
            elem.clear()

        elif elem.tag == 'programme':
            cid = elem.get('channel')
            if not cid:
                elem.clear()
                continue
            title = elem.findtext('title') or 'Unknown Show'
            icon = elem.find('icon')
            logo = icon.get('src') if icon is not None else ''
            try:
                start = parse_xmltv_time(elem.get('start'), convert_to_ist)
                end = parse_xmltv_time(elem.get('stop'), convert_to_ist)
            except Exception:
                elem.clear()
                continue

            if cid not in programmes:
                programmes[cid] = []
            programmes[cid].append({
                'show_name': title,
                'start_time': start,
                'end_time': end,
                'show_logo': logo
            })
            elem.clear()

    # Apply channel filter
    if allowed_channels:
        allowed_lower = [x.lower() for x in allowed_channels]
        channels = {cid: info for cid, info in channels.items() if info['name'].lower() in allowed_lower}
        programmes = {cid: progs for cid, progs in programmes.items() if cid in channels}

    return channels, programmes

def filter_by_date(programmes, target_date):
    midnight = datetime.combine(target_date, datetime.min.time())
    result = []
    for p in programmes:
        s, e = p['start_time'], p['end_time']
        if s.date() == target_date:
            result.append(p)
        elif s.date() == target_date - timedelta(days=1) and e > midnight:
            copy = p.copy()
            copy['start_time'] = midnight
            result.append(copy)
    result.sort(key=lambda x: x['start_time'])
    return result

def process_epg(epg_name, url, is_gz=True, convert_to_ist=False, allowed_channels=None):
    print(f"\n🔄 Processing {epg_name}")
    xml_content = download_epg(url, is_gz)
    channels, programmes = parse_epg_xml(xml_content, allowed_channels, convert_to_ist)

    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    base = epg_name.lower().replace(' ', '_')

    today_path = os.path.join(base, 'today')
    tomorrow_path = os.path.join(base, 'tomorrow')
    os.makedirs(today_path, exist_ok=True)
    os.makedirs(tomorrow_path, exist_ok=True)

    for cid, info in channels.items():
        if cid not in programmes:
            continue

        logo_path = None
        if info['logo']:
            filename = sanitize_filename(info['name']) + '.webp'
            logo_path = os.path.join(ASSETS_DIR, 'logos', filename)
            compress_image_to_webp(info['logo'], logo_path)

        today_progs = filter_by_date(programmes[cid], today)
        tomorrow_progs = filter_by_date(programmes[cid], tomorrow)

        def make_schedule(progs, date):
            return {
                "channel_name": info['name'],
                "channel_logo": logo_path or info['logo'],
                "date": format_date(date),
                "schedule": [{
                    "show_name": p['show_name'],
                    "start_time": format_time(p['start_time']),
                    "end_time": format_time(p['end_time']),
                    "show_logo": compress_image_to_webp(
                        p['show_logo'],
                        os.path.join(ASSETS_DIR, 'shows', sanitize_filename(p['show_name']) + '.webp')
                    ) if p['show_logo'] else ""
                } for p in progs]
            }

        if today_progs:
            json_path = os.path.join(today_path, sanitize_filename(info['name']) + '.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(make_schedule(today_progs, today), f, indent=2, ensure_ascii=False)

        if tomorrow_progs:
            json_path = os.path.join(tomorrow_path, sanitize_filename(info['name']) + '.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(make_schedule(tomorrow_progs, tomorrow), f, indent=2, ensure_ascii=False)

    print(f"✅ {epg_name} processed ({len(channels)} filtered channels)")

def main():
    allowed_channels = read_filter_list(FILTER_FILE)
    epg_sources = [
        {"name": "Tata Play", "url": "https://avkb.short.gy/tsepg.xml.gz", "is_gz": True, "convert_to_ist": True},
        {"name": "Jio TV", "url": "https://avkb.short.gy/jioepg.xml.gz", "is_gz": True, "convert_to_ist": True}
    ]
    for epg in epg_sources:
        process_epg(epg["name"], epg["url"], epg["is_gz"], epg["convert_to_ist"], allowed_channels)

if __name__ == "__main__":
    main()
