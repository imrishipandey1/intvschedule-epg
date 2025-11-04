#!/usr/bin/env python3
"""
EPG -> JSON converter for GitHub Actions.

Behavior:
- Processes only Tata Play and Jio TV EPG sources.
- Reads channel filter from ./filter.txt (one channel name per line).
- Prefers Jio TV if the same channel exists in both sources.
- Saves JSON files into /data/today/ and /data/tomorrow/.
- Non-interactive (suitable for Actions).
"""

import requests
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json
import os
import re
from io import BytesIO

# ---------- Config ----------
EPG_SOURCES = [
    {
        'name': 'Tata Play',
        'url': 'https://avkb.short.gy/tsepg.xml.gz',
        'is_gz': True,
        'convert_to_ist': True
    },
    {
        'name': 'Jio TV',
        'url': 'https://avkb.short.gy/jioepg.xml.gz',
        'is_gz': True,
        'convert_to_ist': True
    }
]
FILTER_FILE = 'filter.txt'
DATA_TODAY_DIR = os.path.join('data', 'today')
DATA_TOMORROW_DIR = os.path.join('data', 'tomorrow')
# ----------------------------

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*]', '-', filename)
    filename = filename.strip()
    return filename.lower().replace(' ', '-') + '.json'

def parse_xmltv_time(time_str, convert_to_ist=False):
    """Parse XMLTV time format (YYYYMMDDHHmmss and optional timezone)."""
    # Format sometimes like: "20251104000000 +0000" or "20251104000000"
    if not time_str:
        raise ValueError("Empty time string")
    dt_str = time_str.split(' ')[0]
    dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S')
    if convert_to_ist:
        dt = dt + timedelta(hours=5, minutes=30)
    return dt

def format_time(dt):
    return dt.strftime('%I:%M %p').lstrip('0')

def format_date(dt):
    return dt.strftime('%B %d, %Y')

def download_gz_epg(url):
    print(f"Downloading: {url}")
    # convert github blob links to raw if any
    if 'github.com' in url and '/blob/' in url:
        url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with gzip.GzipFile(fileobj=BytesIO(resp.content)) as gz:
        xml_content = gz.read()
    return xml_content.decode('utf-8')

def download_xml_epg(url):
    print(f"Downloading: {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text

def parse_epg_xml(xml_content, convert_to_ist=False):
    root = ET.fromstring(xml_content)
    channels = {}
    for channel in root.findall('channel'):
        channel_id = channel.get('id')
        display = channel.find('display-name')
        display_name = display.text if display is not None else channel_id
        icon = channel.find('icon')
        channel_logo = icon.get('src') if icon is not None else ""
        channels[channel_id] = {
            'id': channel_id,
            'name': display_name,
            'logo': channel_logo
        }

    programmes = {}
    for programme in root.findall('programme'):
        channel_id = programme.get('channel')
        if channel_id not in programmes:
            programmes[channel_id] = []

        title = programme.find('title')
        show_name = title.text if title is not None else "Unknown Show"

        start_time = parse_xmltv_time(programme.get('start'), convert_to_ist)
        end_time = parse_xmltv_time(programme.get('stop'), convert_to_ist)

        icon = programme.find('icon')
        show_logo = icon.get('src') if icon is not None else ""

        programmes[channel_id].append({
            'show_name': show_name,
            'start_time': start_time,
            'end_time': end_time,
            'show_logo': show_logo
        })

    return channels, programmes

def filter_programmes_by_date(programmes, target_date):
    filtered = []
    midnight_dt = datetime.combine(target_date, datetime.min.time())
    for prog in programmes:
        start_dt = prog['start_time']
        end_dt = prog['end_time']
        start_date = start_dt.date()
        end_date = end_dt.date()

        if start_date == target_date:
            filtered.append(prog)
        elif start_date == target_date - timedelta(days=1) and end_date == target_date:
            if end_dt > midnight_dt:
                adjusted_prog = prog.copy()
                adjusted_prog['start_time'] = midnight_dt
                filtered.append(adjusted_prog)
    filtered.sort(key=lambda x: x['start_time'])
    return filtered

def create_json_schedule(channel_name, channel_logo, programmes, target_date):
    schedule_data = {
        "channel_name": channel_name,
        "date": format_date(target_date),
        "schedule": []
    }
    for prog in programmes:
        schedule_data["schedule"].append({
            "show_name": prog['show_name'],
            "start_time": format_time(prog['start_time']),
            "end_time": format_time(prog['end_time']),
            "show_logo": prog['show_logo']
        })
    return schedule_data

def read_filter_list(path):
    if not os.path.exists(path):
        print(f"Warning: {path} not found. No channels will be processed.")
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        names = [line.strip() for line in f if line.strip()]
    # normalize
    return set([n.lower() for n in names])

def ensure_dirs():
    os.makedirs(DATA_TODAY_DIR, exist_ok=True)
    os.makedirs(DATA_TOMORROW_DIR, exist_ok=True)

def main():
    print("=" * 60)
    print("EPG -> JSON (GitHub Actions friendly)".center(60))
    print("=" * 60)

    filter_names = read_filter_list(FILTER_FILE)
    if not filter_names:
        print("No channel names found in filter.txt — exiting.")
        return

    ensure_dirs()

    # We'll collect mapping by normalized channel name -> {info, programmes}
    # Order of processing: Tata Play first, then Jio TV so Jio overwrites Tata if same channel exists.
    aggregated = {}  # normalized_channel_name -> {'name':orig, 'logo':..., 'programmes': [...]}

    for epg in EPG_SOURCES:
        print(f"\nProcessing source: {epg['name']}")
        try:
            if epg.get('is_gz', True):
                xml = download_gz_epg(epg['url'])
            else:
                xml = download_xml_epg(epg['url'])
            channels_map, programmes_map = parse_epg_xml(xml, epg.get('convert_to_ist', False))
            count = 0
            for ch_id, ch_info in channels_map.items():
                norm_name = ch_info['name'].strip().lower()
                # Only consider channels listed in filter.txt
                if norm_name not in filter_names:
                    continue
                # gather programmes (may be empty)
                progs = programmes_map.get(ch_id, [])
                if not progs:
                    continue
                # Save/overwrite depending on source order (Jio processed last -> preferred)
                aggregated[norm_name] = {
                    'name': ch_info['name'],
                    'logo': ch_info.get('logo', ''),
                    'programmes': progs
                }
                count += 1
            print(f"  - matched & collected {count} filtered channels from {epg['name']}")
        except Exception as e:
            print(f"  ✗ Error processing {epg['name']}: {e}")

    if not aggregated:
        print("No channels collected after processing sources. Exiting.")
        return

    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)

    written = 0
    for norm_name, ch in aggregated.items():
        # filter programmes for today and tomorrow
        today_progs = filter_programmes_by_date(ch['programmes'], today)
        tomorrow_progs = filter_programmes_by_date(ch['programmes'], tomorrow)

        filename = sanitize_filename(ch['name'])
        if today_progs:
            today_schedule = create_json_schedule(ch['name'], ch['logo'], today_progs, today)
            today_path = os.path.join(DATA_TODAY_DIR, filename)
            with open(today_path, 'w', encoding='utf-8') as f:
                json.dump(today_schedule, f, indent=2, ensure_ascii=False)
            written += 1

        if tomorrow_progs:
            tomorrow_schedule = create_json_schedule(ch['name'], ch['logo'], tomorrow_progs, tomorrow)
            tomorrow_path = os.path.join(DATA_TOMORROW_DIR, filename)
            with open(tomorrow_path, 'w', encoding='utf-8') as f:
                json.dump(tomorrow_schedule, f, indent=2, ensure_ascii=False)
            written += 1

    print(f"\n✓ Done. Written {written} JSON file(s) into:")
    print(f"  - {DATA_TODAY_DIR}")
    print(f"  - {DATA_TOMORROW_DIR}")
    print("=" * 60)

if __name__ == '__main__':
    main()
