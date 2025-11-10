import os
import json
import base64
import logging
from glob import glob
from typing import List, Dict, Any, Optional

import requests

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ---------- Configuration (from env) ----------
WP_BASE_URL = os.environ.get('https://intvschedule.com')  # e.g. https://intvschedule.com
WP_USER = os.environ.get('itsrishipandey')
WP_APP_PASSWORD = os.environ.get('kdJu jK6c 013L ZiiH GIgA zjGj')
POST_TYPE = os.environ.get('POST_TYPE', 'channel')  # change if your CPT slug is different

# Local repo directories (relative to repo root)
TODAY_DIR = os.environ.get('TODAY_DIR', 'output-today')
TOMORROW_DIR = os.environ.get('TOMORROW_DIR', 'output-tomorrow')

if not WP_BASE_URL or not WP_USER or not WP_APP_PASSWORD:
    logger.error('Missing required environment variables. Please set WP_BASE_URL, WP_USER, and WP_APP_PASSWORD.')
    raise SystemExit(1)

# Basic auth header for Application Passwords
def make_auth_header(user: str, app_password: str) -> Dict[str, str]:
    token = base64.b64encode(f"{user}:{app_password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

AUTH_HEADERS = make_auth_header(WP_USER, WP_APP_PASSWORD)

# ---------- Helper functions ----------

def slug_from_filename(path: str) -> str:
    name = os.path.basename(path)
    if name.lower().endswith('.json'):
        return name[:-5]
    return name


def get_post_id_by_slug(slug: str) -> Optional[int]:
    """Query WP REST for post with this slug in the configured post type."""
    url = f"{WP_BASE_URL}/wp-json/wp/v2/{POST_TYPE}"
    params = {'slug': slug}
    logger.debug(f'Querying for slug {slug} at {url}')
    r = requests.get(url, params=params, headers=AUTH_HEADERS)
    if r.status_code != 200:
        logger.warning(f'WP query returned {r.status_code} for slug {slug}: {r.text[:200]}')
        return None
    data = r.json()
    if not data:
        logger.info(f'No post found with slug: {slug}')
        return None
    post_id = data[0].get('id')
    logger.info(f'Found post id {post_id} for slug {slug}')
    return post_id


def update_acf_fields(post_id: int, fields_payload: Dict[str, Any]) -> bool:
    """Try update via ACF REST endpoint. Fallbacks included."""
    # Primary guess: endpoint for CPTs often exposed as /wp-json/acf/v3/{post_type}/{id}
    endpoints = [
        f"{WP_BASE_URL}/wp-json/acf/v3/{POST_TYPE}/{post_id}",
        f"{WP_BASE_URL}/wp-json/acf/v3/posts/{post_id}",
    ]

    payload = {"fields": fields_payload}

    for ep in endpoints:
        logger.debug(f'Trying ACF endpoint: {ep}')
        r = requests.post(ep, headers=AUTH_HEADERS, data=json.dumps(payload))
        if r.status_code in (200, 201):
            logger.info(f'Successfully updated ACF for post {post_id} using {ep}')
            return True
        else:
            logger.warning(f'ACF update failed at {ep}: {r.status_code} {r.text[:300]}')
    return False


# ---------- Mapping helpers ----------

def make_repeater_rows(programs: List[Dict[str, Any]], date_value: str) -> List[Dict[str, Any]]:
    rows = []
    for p in programs:
        # Normalize keys and provide defaults
        row = {
            'show_name': p.get('show_name') or p.get('title') or '',
            'show_logo': p.get('show_logo') or p.get('logo') or '',
            'start_time': p.get('start_time') or p.get('start') or '',
            'end_time': p.get('end_time') or p.get('end') or '',
            'show_date': date_value,
        }
        rows.append(row)
    return rows


# ---------- Main processing ----------

def process_file(path: str, is_today: bool = True) -> None:
    logger.info(f'Processing file: {path} (today={is_today})')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f'Failed to load JSON {path}: {e}')
        return

    slug = slug_from_filename(path)
    post_id = get_post_id_by_slug(slug)
    if not post_id:
        logger.error(f'Cannot find post for slug {slug}. Skipping.')
        return

    date_value = data.get('date') or ''
    programs = data.get('programs') or []

    today_rows = make_repeater_rows(programs, date_value)

    fields_payload = {}
    if is_today:
        fields_payload['schedule_repeater'] = today_rows
    else:
        fields_payload['schedule_tomorrow'] = today_rows

    success = update_acf_fields(post_id, fields_payload)
    if not success:
        logger.error(f'Failed to update ACF for post {post_id} ({slug})')


def process_directory(directory: str, is_today: bool) -> None:
    if not os.path.isdir(directory):
        logger.warning(f'Directory does not exist: {directory}. Skipping.')
        return
    files = sorted(glob(os.path.join(directory, '*.json')))
    logger.info(f'Found {len(files)} json files in {directory}')
    for f in files:
        process_file(f, is_today=is_today)


if __name__ == '__main__':
    logger.info('Starting ACF importer')
    # Process today folder
    process_directory(TODAY_DIR, is_today=True)
    # Process tomorrow folder
    process_directory(TOMORROW_DIR, is_today=False)
    logger.info('Done')
