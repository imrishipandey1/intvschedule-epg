#!/usr/bin/env python3
import requests
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json
import os
import re
from io import BytesIO

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*]', '-', filename)
    filename = filename.strip()
    return filename.lower().replace(' ', '-') + '.json'

def parse_xmltv_time(time_str, convert_to_ist=False):
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
    if 'github.com' in url and '/blob/' in url:
        url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
    response = requests.get(url)
    response.raise_for_status()
    with gzip.GzipFile(fileobj=BytesIO(response.content)) as gz:
        xml_content = gz.read()
    return xml_content.decode('utf-8')

def download_xml_epg(url):
    print(f"Downloading: {url}")
    response = requests.get(url)
    response.raise_for_status()
    return response.text

def parse_epg_xml(xml_content, convert_to_ist=False):
    root = ET.fromstring(xml_content)
    channels = {}
    for channel in root.findall('channel'):
        channel_id = channel.get('id')
        display_name = channel.find('display-name').text if channel.find('display-name') is not None else channel_id
        icon = channel.find('icon')
        channel_logo = icon.get('src') if icon is not None else ""
        channels[channel_id] = {'name': display_name, 'logo': channel_logo}

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

def process_epg(epg_name, epg_url, is_gz=True, convert_to_ist=False):
    print(f"\nProcessing {epg_name} EPG...")
    if convert_to_ist:
        print(f"  Converting UTC to IST (UTC+5:30)")
    try:
        if is_gz:
            xml_content = download_gz_epg(epg_url)
        else:
            xml_content = download_xml_epg(epg_url)
        channels, programmes = parse_epg_xml(xml_content, convert_to_ist)
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        base_dir = epg_name.lower().replace(' ', '_')
        today_dir = os.path.join(base_dir, 'today')
        tomorrow_dir = os.path.join(base_dir, 'tomorrow')
        os.makedirs(today_dir, exist_ok=True)
        os.makedirs(tomorrow_dir, exist_ok=True)
        channel_count = 0
        for channel_id, channel_info in channels.items():
            if channel_id in programmes:
                channel_progs = programmes[channel_id]
                today_progs = filter_programmes_by_date(channel_progs, today)
                if today_progs:
                    today_schedule = create_json_schedule(channel_info['name'], channel_info['logo'], today_progs, today)
                    filename = sanitize_filename(channel_info['name'])
                    filepath = os.path.join(today_dir, filename)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(today_schedule, f, indent=2, ensure_ascii=False)
                tomorrow_progs = filter_programmes_by_date(channel_progs, tomorrow)
                if tomorrow_progs:
                    tomorrow_schedule = create_json_schedule(channel_info['name'], channel_info['logo'], tomorrow_progs, tomorrow)
                    filename = sanitize_filename(channel_info['name'])
                    filepath = os.path.join(tomorrow_dir, filename)
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(tomorrow_schedule, f, indent=2, ensure_ascii=False)
                channel_count += 1
        print(f"✓ {epg_name}: Processed {channel_count} channels")
        print(f"  - Today's schedules saved in: {today_dir}")
        print(f"  - Tomorrow's schedules saved in: {tomorrow_dir}")
    except Exception as e:
        print(f"✗ Error processing {epg_name}: {str(e)}")

def main():
    print("=" * 60)
    print("EPG to JSON Converter")
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
        }
    ]
    for epg in epg_sources:
        process_epg(epg['name'], epg['url'], epg['is_gz'], epg.get('convert_to_ist', False))
    print("\n" + "=" * 60)
    print("Processing complete!")
    print("=" * 60)
    for epg in epg_sources:
        folder_name = epg['name'].lower().replace(' ', '_')
        print(f"├── {folder_name}/")
        print(f"│   ├── today/")
        print(f"│   └── tomorrow/")

if __name__ == "__main__":
    main()
