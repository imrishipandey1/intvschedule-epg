# 🚀 Deployment Guide for EPG Parser

## Quick Start (5 Minutes)

### Step 1: Create GitHub Repository

1. Go to [GitHub](https://github.com/new)
2. Repository name: `epg-parser` (or any name you prefer)
3. Set to **Public** or **Private**
4. ✅ Check "Add a README file" (will be overwritten)
5. Click **Create repository**

### Step 2: Upload Files

**Option A: Using GitHub Web Interface**

1. Click **Add file** → **Upload files**
2. Drag and drop these files:
   - `epg_parser.go`
   - `filter.txt`
   - `go.mod`
   - `.gitignore`
   - `README.md`
3. Create folder structure for workflow:
   - Click **Add file** → **Create new file**
   - Type: `.github/workflows/epg-parser.yml`
   - Paste the workflow content
4. Commit changes

**Option B: Using Git Command Line**

```bash
# Clone your empty repository
git clone https://github.com/YOUR_USERNAME/epg-parser.git
cd epg-parser

# Copy all files into this directory
# Then commit and push
git add .
git commit -m "Initial commit: EPG parser setup"
git push origin main
```

### Step 3: Enable Workflow Permissions

1. Go to **Settings** → **Actions** → **General**
2. Scroll to **Workflow permissions**
3. Select: ✅ **Read and write permissions**
4. ✅ Check: **Allow GitHub Actions to create and approve pull requests**
5. Click **Save**

### Step 4: Test the Workflow

**Manual Trigger:**

1. Go to **Actions** tab
2. Click **Parse EPG and Generate JSON** workflow
3. Click **Run workflow** → **Run workflow**
4. Wait 2-3 minutes for completion

**Check Results:**

1. Go to the Actions run page
2. Click on the completed workflow
3. Expand **Run EPG Parser** step
4. You should see output like:
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

5. Check your repository - new folders `output-today/` and `output-tomorrow/` should appear with JSON files

## ⏰ Automatic Schedule

The workflow runs automatically every day at **1:30 AM IST**.

To verify:
- Check **.github/workflows/epg-parser.yml**
- Look for: `cron: '0 20 * * *'` (20:00 UTC = 1:30 AM IST next day)

## 📝 Customizing Channels

Edit `filter.txt` in your repository:

1. Click on `filter.txt`
2. Click the **pencil icon** (Edit)
3. Add/remove channel names (one per line)
4. Click **Commit changes**

### Filter Examples

```txt
# Simple channel names (auto-generates filename)
Sony SAB
Star Plus
Colors TV
Zee TV

# Explicit filename
9x-jhakaas.json
colors-cineplex.json

# Rename output (use data from A, save as B)
sony-sab-hd.json=sony-sab.json
star-plus-hd.json=star-plus.json

# More channels
Discovery Channel
National Geographic
MTV India
Sony Entertainment Television
Zee Cinema
Star Movies
```

**How matching works:**
- Case-insensitive: `Sony SAB` matches `SONY SAB` or `sony sab`
- Ignores spaces/dashes: `Star Plus` matches `StarPlus` or `star-plus`
- Smart parsing: `9x-jhakaas.json` → looks for channel "9x Jhakaas"

## 🔍 Finding Channel Names

### Method 1: Check EPG Sources Directly

Run locally to see all available channels:

```go
// Modify epg_parser.go temporarily - add after merging channels:
for id, info := range channelMap {
    fmt.Printf("ID: %s | Name: %s\n", id, info.Name)
}
```

### Method 2: Trial and Error

1. Add a channel name to `filter.txt`
2. Run workflow
3. Check if JSON is generated
4. If not, try variations (remove "HD", change spacing, etc.)

## 📥 Accessing JSON Files

### Download Directly from GitHub

1. Navigate to `output-today/` or `output-tomorrow/`
2. Click on any `.json` file
3. Click **Raw** button
4. Copy the URL (e.g., `https://raw.githubusercontent.com/USERNAME/REPO/main/output-today/sony-sab.json`)

### Use in Your Application

```javascript
// Fetch JSON in JavaScript
fetch('https://raw.githubusercontent.com/USERNAME/REPO/main/output-today/sony-sab.json')
  .then(response => response.json())
  .then(data => {
    console.log(data.channel_name);
    console.log(data.programs);
  });
```

```php
// Fetch JSON in PHP
$url = 'https://raw.githubusercontent.com/USERNAME/REPO/main/output-today/sony-sab.json';
$json = file_get_contents($url);
$data = json_decode($json, true);

echo $data['channel_name'];
print_r($data['programs']);
```

### Using GitHub Pages (Optional)

Enable GitHub Pages to serve JSON files over HTTPS:

1. Go to **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** | Folder: **/ (root)**
4. Click **Save**
5. Access files at: `https://USERNAME.github.io/REPO/output-today/sony-sab.json`

## 🛠️ Advanced Configuration

### Change Execution Time

Edit `.github/workflows/epg-parser.yml`:

```yaml
on:
  schedule:
    - cron: '0 20 * * *'  # Current: 1:30 AM IST
```

**Common schedules:**

| Time (IST)  | Cron Expression | Time (UTC) |
|-------------|-----------------|------------|
| 12:00 AM    | `30 18 * * *`   | 6:30 PM    |
| 1:30 AM     | `0 20 * * *`    | 8:00 PM    |
| 6:00 AM     | `30 0 * * *`    | 12:30 AM   |
| 12:00 PM    | `30 6 * * *`    | 6:30 AM    |

**Note:** Cron uses UTC time. IST = UTC + 5:30

### Run Multiple Times Daily

```yaml
on:
  schedule:
    - cron: '0 20 * * *'   # 1:30 AM IST
    - cron: '30 6 * * *'   # 12:00 PM IST
    - cron: '0 12 * * *'   # 5:30 PM IST
```

### Add Next 7 Days Output

Modify `epg_parser.go` to add a loop:

```go
// After defining today and tomorrow
for i := 0; i < 7; i++ {
    targetDate := today.AddDate(0, 0, i)
    folderName := fmt.Sprintf("output-day-%d", i)
    // ... process and save
}
```

## 🐛 Common Issues

### Issue 1: Workflow Not Running

**Symptom:** No automatic runs at scheduled time

**Solutions:**
- Check repository has activity (GitHub disables workflows on inactive repos after 60 days)
- Verify workflow permissions are set correctly
- Ensure `.github/workflows/epg-parser.yml` is in the correct path
- Check if Actions are enabled for your repository

### Issue 2: No JSON Files Generated

**Symptom:** Workflow completes but no output files

**Solutions:**
- Check workflow logs for errors
- Verify `filter.txt` channel names match EPG data
- Try simpler channel names (e.g., "Sony SAB" instead of "Sony SAB HD")
- Check if channels exist in EPG sources

### Issue 3: Empty JSON Files

**Symptom:** JSON files created but with empty programs array

**Solutions:**
- Check if the channel has data for the target date
- Verify time zone conversion is working
- Try running for a different date

### Issue 4: Git Push Fails

**Symptom:** Error: "failed to push some refs"

**Solutions:**
- Check workflow permissions (Settings → Actions → Read and write)
- Ensure default branch is `main` (not `master`)
- Check if branch protection rules prevent Actions from pushing

## 📊 Monitoring

### View Workflow History

1. Go to **Actions** tab
2. See all workflow runs with timestamps
3. Click any run to see detailed logs

### Enable Notifications

1. Go to your repository
2. Click **Watch** → **Custom**
3. ✅ Check **Actions** (receive emails when workflows fail)

### Check File Changes

1. Go to **Commits** or **Code** tab
2. See when JSON files were last updated
3. Click on a commit to see file changes

## 🔒 Security Notes

- EPG sources are public (Jio TV and Tata Play)
- No authentication required
- Repository can be private (JSON access via personal tokens)
- Or public (JSON access via raw.githubusercontent.com)

## 📞 Support

If you encounter issues:

1. Check workflow logs in Actions tab
2. Verify all files are in correct locations
3. Test locally with `go run epg_parser.go`
4. Check that Go version matches (1.23)

## ✅ Checklist

Before going live:

- [ ] Repository created on GitHub
- [ ] All files uploaded (7 files total)
- [ ] Workflow permissions enabled (Read and write)
- [ ] `filter.txt` customized with your channels
- [ ] Manual workflow test successful
- [ ] Output folders created with JSON files
- [ ] Scheduled time confirmed (1:30 AM IST)
- [ ] JSON file URLs working

## 🎉 You're Done!

Your EPG parser is now running automatically. New JSON files will be generated daily at 1:30 AM IST.

**Next steps:**
- Integrate JSON files into your TV schedule website
- Add more channels to `filter.txt`
- Customize output format if needed
