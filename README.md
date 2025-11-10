# EPG Parser for Indian TV Channels

Automated EPG (Electronic Program Guide) parser that downloads, merges, and processes TV schedules from Jio TV and Tata Play, generating daily JSON files for filtered channels.

## 🚀 Features

- **Automated Daily Updates**: Runs every day at 1:30 AM IST via GitHub Actions
- **Dual Source Merging**: Combines EPG data from Jio TV and Tata Play with smart priority handling
- **Time Zone Conversion**: Converts UTC timestamps to Indian Standard Time (IST)
- **Flexible Filtering**: Channel-based filtering with custom naming rules
- **Daily JSON Output**: Generates separate schedules for today and tomorrow

## 📂 Project Structure

```
.
├── .github/
│   └── workflows/
│       └── epg-parser.yml       # GitHub Actions workflow
├── epg_parser.go                # Main Go script
├── filter.txt                   # Channel filter configuration
├── output-today/                # Generated: Today's schedules
│   ├── sony-sab.json
│   ├── star-plus.json
│   └── ...
└── output-tomorrow/             # Generated: Tomorrow's schedules
    ├── sony-sab.json
    ├── star-plus.json
    └── ...
```

## 🛠️ Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
cd YOUR_REPO_NAME
```

### 2. Configure filter.txt

Edit `filter.txt` to add your desired channels. Each line represents one channel:

```
Sony SAB
Star Plus
Colors TV
9x-jhakaas.json
sony-sab-hd.json=sony-sab.json
```

**Filter Rules:**
- Simple channel name: `Sony SAB` → outputs `sony-sab.json`
- With extension: `9x-jhakaas.json` → channel "9x Jhakaas" → outputs `9x-jhakaas.json`
- Rename mapping: `sony-sab-hd.json=sony-sab.json` → uses "Sony SAB HD" data but saves as `sony-sab.json`

### 3. Enable GitHub Actions

1. Go to your repository on GitHub
2. Click on **Settings** → **Actions** → **General**
3. Under "Workflow permissions", select **Read and write permissions**
4. Click **Save**

### 4. Manual Trigger (Optional)

You can manually trigger the workflow:

1. Go to **Actions** tab in your repository
2. Select **Parse EPG and Generate JSON**
3. Click **Run workflow**

## ⚙️ How It Works

### Workflow Schedule

- **Cron Schedule**: `0 20 * * *` (8:00 PM UTC = 1:30 AM IST next day)
- **Manual Trigger**: Available via GitHub Actions UI

### Data Sources

1. **Jio TV EPG**: `https://avkb.short.gy/jioepg.xml.gz` (Priority)
2. **Tata Play EPG**: `https://avkb.short.gy/tsepg.xml.gz` (Fallback)

### Processing Pipeline

1. **Download**: Fetches both EPG files (GZ compressed XML)
2. **Decompress**: Extracts XML data from GZ archives
3. **Parse**: Processes XML structure (channels and programmes)
4. **Merge**: Combines data with Jio TV priority
5. **Filter**: Matches channels from `filter.txt`
6. **Convert**: UTC → IST time conversion
7. **Generate**: Creates JSON files for today and tomorrow

### JSON Output Format

Each channel gets a JSON file with this structure:

```json
{
  "channel_name": "Sony SAB",
  "channel_logo": "https://jiotv.catchup.cdn.jio.com/dare_images/images/Sony_SAB.png",
  "date": "2025-11-11",
  "programs": [
    {
      "show_name": "Taarak Mehta Ka Ooltah Chashmah",
      "start_time": "06:30 PM",
      "end_time": "07:00 PM",
      "show_logo": "https://jiotv.catchup.cdn.jio.com/dare_images/shows/2025-11-03/251103154000.jpg"
    },
    {
      "show_name": "Baalveer Returns",
      "start_time": "07:00 PM",
      "end_time": "07:30 PM",
      "show_logo": ""
    }
  ]
}
```

## 🧪 Local Testing

### Prerequisites

- Go 1.23 or higher
- Internet connection

### Run Locally

```bash
# Ensure filter.txt exists
go run epg_parser.go
```

Expected output:
```
🚀 Starting EPG Parser...
📅 Today (IST): 2025-11-11
📅 Tomorrow (IST): 2025-11-12

📥 Downloading Jio TV EPG...
✅ Jio TV: 543 channels, 12456 programmes

📥 Downloading Tata Play EPG...
✅ Tata Play: 621 channels, 15234 programmes

📋 Reading filter.txt...
✅ Loaded 10 filter rules

✨ Processed: 10 channels | Saved Today: 8 | Saved Tomorrow: 8
```

## 📋 XML Data Structure

### Channel Format
```xml
<channel id="154">
  <display-name>Sony SAB</display-name>
  <icon src="https://jiotv.catchup.cdn.jio.com/dare_images/images/Sony_SAB.png"></icon>
</channel>
```

### Programme Format
```xml
<programme start="20251102183000 +0000" stop="20251102190000 +0000" channel="154">
  <title>Taarak Mehta Ka Ooltah Chashmah</title>
  <desc>...</desc>
  <category>Series</category>
  <date>20251102</date>
  <icon src="https://jiotv.catchup.cdn.jio.com/dare_images/shows/2025-11-03/251103154000.jpg"></icon>
</programme>
```

## 🔧 Customization

### Change Schedule Time

Edit `.github/workflows/epg-parser.yml`:

```yaml
schedule:
  - cron: '0 20 * * *'  # Change this cron expression
```

**Common Times (UTC → IST):**
- `0 18 * * *` = 11:30 PM IST
- `0 20 * * *` = 1:30 AM IST (next day)
- `30 1 * * *` = 7:00 AM IST

### Modify Go Version

Edit `.github/workflows/epg-parser.yml`:

```yaml
- name: Set up Go
  uses: actions/setup-go@v5
  with:
    go-version: '1.23'  # Change version here
```

## 🐛 Troubleshooting

### No JSON files generated

- Check if `filter.txt` contains valid channel names
- Verify channel names match those in EPG sources
- Check GitHub Actions logs for errors

### Time mismatch issues

- Ensure `Asia/Kolkata` timezone is correctly loaded
- Verify EPG source timestamps are in UTC format

### GitHub Actions not running

- Check workflow permissions (Settings → Actions)
- Verify cron schedule syntax
- Check repository has GitHub Actions enabled

## 📝 Notes

- **Empty Schedules**: Channels with no programmes for a given day are skipped
- **File Overwrite**: All JSON files are regenerated on each run
- **Case Insensitive**: Channel matching ignores case and special characters
- **Deduplication**: Duplicate programmes (same time + title) are automatically removed

## 📄 License

This project is open source and available under the MIT License.
