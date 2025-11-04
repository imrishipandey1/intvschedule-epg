import requests
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import json
import os
import re
from io import BytesIO

def sanitize_filename(filename):
    """Sanitize filename for cross-platform compatibility"""
    filename = re.sub(r'[<>:"/\\|?*]', '-', filename)
    filename = filename.strip()
    return filename.lower().replace(' ', '-')

def load_filter_channels():
    """Load channel names from filter.txt"""
    try:
        with open('filter.txt', 'r', encoding='utf-8') as f:
            channels = [line.strip() for line in f if line.strip()]
        print(f"Loaded {len(channels)} channels from filter.txt")
        return channels
    except FileNotFoundError:
        print("Warning: filter.txt not found, processing all channels")
        return None

def parse_xmltv_time(time_str, convert_to_ist=False):
    """Parse XMLTV time format"""
    dt_str = time_str.split(' ')[0]
    dt = datetime.strptime(dt_str, '%Y%m%d%H%M%S')
    
    if convert_to_ist:
        dt = dt + timedelta(hours=5, minutes=30)
    
    return dt

def format_time(dt):
    """Format datetime to 12-hour format"""
    return dt.strftime('%I:%M %p').lstrip('0')

def format_date(dt):
    """Format date to 'Month DD, YYYY' format"""
    return dt.strftime('%B %d, %Y')

def download_gz_epg(url):
    """Download and decompress .gz file"""
    print(f"  Downloading: {url}")
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    
    with gzip.GzipFile(fileobj=BytesIO(response.content)) as gz:
        xml_content = gz.read()
    
    return xml_content.decode('utf-8')

def parse_epg_xml(xml_content, convert_to_ist=False, filter_channels=None):
    """Parse EPG XML and extract channel and programme data"""
    root = ET.fromstring(xml_content)
    
    channels = {}
    for channel in root.findall('channel'):
        channel_id = channel.get('id')
        display_name = channel.find('display-name').text if channel.find('display-name') is not None else channel_id
        
        # Filter channels if filter list provided
        if filter_channels and display_name not in filter_channels:
            continue
        
        icon = channel.find('icon')
        channel_logo = icon.get('src') if icon is not None else ""
        
        channels[channel_id] = {
            'name': display_name,
            'logo': channel_logo
        }
    
    programmes = {}
    for programme in root.findall('programme'):
        channel_id = programme.get('channel')
        
        # Skip if channel not in our filtered list
        if channel_id not in channels:
            continue
        
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
    """Filter programmes for a specific date"""
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

def create_json_schedule(channel_name, programmes, target_date):
    """Create JSON schedule"""
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

def merge_schedules(jio_data, tata_data, filter_channels):
    """Merge Jio and Tata schedules, prioritizing Jio TV data"""
    merged = {}
    
    # Start with Jio TV data (priority)
    for channel_id, channel_info in jio_data['channels'].items():
        if filter_channels and channel_info['name'] not in filter_channels:
            continue
        merged[channel_info['name']] = {
            'channel_info': channel_info,
            'programmes': jio_data['programmes'].get(channel_id, []),
            'source': 'jio'
        }
    
    # Add Tata Play data only for channels not in Jio
    for channel_id, channel_info in tata_data['channels'].items():
        if filter_channels and channel_info['name'] not in filter_channels:
            continue
        if channel_info['name'] not in merged:
            merged[channel_info['name']] = {
                'channel_info': channel_info,
                'programmes': tata_data['programmes'].get(channel_id, []),
                'source': 'tata'
            }
    
    return merged

def main():
    """Main function"""
    print("=" * 60)
    print("EPG Processor (Lightweight)")
    print("=" * 60)
    
    # Load filter
    filter_channels = load_filter_channels()
    
    # EPG sources (Jio first for priority)
    epg_sources = [
        {
            'name': 'Jio TV',
            'url': 'https://avkb.short.gy/jioepg.xml.gz',
            'convert_to_ist': True
        },
        {
            'name': 'Tata Play',
            'url': 'https://avkb.short.gy/tsepg.xml.gz',
            'convert_to_ist': True
        }
    ]
    
    # Download and parse EPGs
    all_data = {}
    for epg in epg_sources:
        print(f"\nProcessing {epg['name']}...")
        try:
            xml_content = download_gz_epg(epg['url'])
            channels, programmes = parse_epg_xml(xml_content, epg['convert_to_ist'], filter_channels)
            all_data[epg['name'].lower().replace(' ', '_')] = {
                'channels': channels,
                'programmes': programmes
            }
            print(f"✓ Found {len(channels)} filtered channels")
        except Exception as e:
            print(f"✗ Error: {str(e)}")
            all_data[epg['name'].lower().replace(' ', '_')] = {'channels': {}, 'programmes': {}}
    
    # Merge schedules (Jio priority)
    print("\nMerging schedules (Jio TV priority)...")
    merged_data = merge_schedules(
        all_data.get('jio_tv', {'channels': {}, 'programmes': {}}),
        all_data.get('tata_play', {'channels': {}, 'programmes': {}}),
        filter_channels
    )
    print(f"✓ Merged {len(merged_data)} channels")
    
    # Generate JSON files
    print("\nGenerating schedule JSON files...")
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    
    output_dir = 'output'
    schedules_dir = os.path.join(output_dir, 'schedules')
    today_dir = os.path.join(schedules_dir, 'today')
    tomorrow_dir = os.path.join(schedules_dir, 'tomorrow')
    
    os.makedirs(today_dir, exist_ok=True)
    os.makedirs(tomorrow_dir, exist_ok=True)
    
    for channel_name, data in merged_data.items():
        channel_slug = sanitize_filename(channel_name)
        
        # Today's schedule
        today_progs = filter_programmes_by_date(data['programmes'], today)
        if today_progs:
            schedule = create_json_schedule(channel_name, today_progs, today)
            with open(os.path.join(today_dir, f"{channel_slug}.json"), 'w', encoding='utf-8') as f:
                json.dump(schedule, f, indent=2, ensure_ascii=False)
        
        # Tomorrow's schedule
        tomorrow_progs = filter_programmes_by_date(data['programmes'], tomorrow)
        if tomorrow_progs:
            schedule = create_json_schedule(channel_name, tomorrow_progs, tomorrow)
            with open(os.path.join(tomorrow_dir, f"{channel_slug}.json"), 'w', encoding='utf-8') as f:
                json.dump(schedule, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Generated schedules for {len(merged_data)} channels")
    print("\n" + "=" * 60)
    print("Processing complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
