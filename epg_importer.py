#!/usr/bin/env python3
import requests
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json
import os
import re
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

# ---------- CONFIG ----------
MAX_WORKERS = 3  # number of parallel downloads
IST_OFFSET = timedelta(hours=5, minutes=30)
# ----------------------------

def sanitize_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '-', name)
    name = name.strip().lower().replace(' ', '-')
    return name + '.json'

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
    print(f"⬇️  Downloading {url}")
    if 'github.com' in url and '/blob/' in url:
        url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    if is_gz:
        with gzip.GzipFile(fileobj=BytesIO(r.content)) as gz:
            return gz.read().decode('utf-8')
    return r.text

def stream_parse_epg(xml_content, convert_to_ist=False):
    """Memory-efficient parsing using iterparse"""
    channels = {}
    programmes = {}

    for event, elem in ET.iterparse(BytesIO(xml_content.encode('utf-8')), events=('end',)):
        if elem.tag == 'channel':
            cid = elem.get('id')
            name = (elem.findtext('display-name') or cid).strip()
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

def process_epg(epg_name, url, is_gz=True, convert_to_ist=False):
    print(f"\n🔄 Processing {epg_name}")
    try:
        xml_content = download_epg(url, is_gz)
        channels, programmes = stream_parse_epg(xml_content, convert_to_ist)
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)

        base = epg_name.lower().replace(' ', '_')
        today_path = os.path.join(base, 'today')
        tomorrow_path = os.path.join(base, 'tomorrow')
        os.makedirs(today_path, exist_ok=True)
        os.makedirs(tomorrow_path, exist_ok=True)

        today_all, tomorrow_all = [], []
        for cid, info in channels.items():
            if cid not in programmes:
                continue
            today_shows = filter_by_date(programmes[cid], today)
            tomorrow_shows = filter_by_date(programmes[cid], tomorrow)
            if today_shows:
                today_all.append({
                    "channel_name": info['name'],
                    "channel_logo": info['logo'],
                    "schedule": [{
                        "show_name": p['show_name'],
                        "start_time": format_time(p['start_time']),
                        "end_time": format_time(p['end_time']),
                        "show_logo": p['show_logo']
                    } for p in today_shows]
                })
            if tomorrow_shows:
                tomorrow_all.append({
                    "channel_name": info['name'],
                    "channel_logo": info['logo'],
                    "schedule": [{
                        "show_name": p['show_name'],
                        "start_time": format_time(p['start_time']),
                        "end_time": format_time(p['end_time']),
                        "show_logo": p['show_logo']
                    } for p in tomorrow_shows]
                })

        # Write one JSON per day
        with open(os.path.join(today_path, 'today.json'), 'w', encoding='utf-8') as f:
            json.dump({
                "source": epg_name,
                "date": format_date(today),
                "channels": today_all
            }, f, indent=2, ensure_ascii=False)

        with open(os.path.join(tomorrow_path, 'tomorrow.json'), 'w', encoding='utf-8') as f:
            json.dump({
                "source": epg_name,
                "date": format_date(tomorrow),
                "channels": tomorrow_all
            }, f, indent=2, ensure_ascii=False)

        print(f"✅ {epg_name} done → {len(channels)} channels processed")

    except Exception as e:
        print(f"❌ {epg_name} failed: {e}")

def main():
    print("=" * 60)
    print("FAST EPG ➜ JSON Converter (Optimized)")
    print("=" * 60)

    epg_sources = [
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
        },
        {
            'name': 'UK Channels',
            'url': 'https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml',
            'is_gz': False,
            'convert_to_ist': False
        }
    ]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        executor.map(lambda e: process_epg(e['name'], e['url'], e['is_gz'], e['convert_to_ist']), epg_sources)

    print("\n✅ All EPG sources processed.")
    print("=" * 60)

if __name__ == "__main__":
    main()
