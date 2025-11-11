package main

import (
	"compress/gzip"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// XML structures
type TV struct {
	XMLName    xml.Name    `xml:"tv"`
	Channels   []Channel   `xml:"channel"`
	Programmes []Programme `xml:"programme"`
}

type Channel struct {
	ID          string `xml:"id,attr"`
	DisplayName string `xml:"display-name"`
	Icon        Icon   `xml:"icon"`
}

type Programme struct {
	Start   string `xml:"start,attr"`
	Stop    string `xml:"stop,attr"`
	Channel string `xml:"channel,attr"`
	Title   string `xml:"title"`
	Desc    string `xml:"desc"`
	Icon    Icon   `xml:"icon"`
}

type Icon struct {
	Src string `xml:"src,attr"`
}

// JSON structures
type ChannelJSON struct {
	ChannelName string        `json:"channel_name"`
	ChannelLogo string        `json:"channel_logo"`
	Date        string        `json:"date"`
	Programs    []ProgramJSON `json:"programs"`
}

type ProgramJSON struct {
	ShowName  string `json:"show_name"`
	StartTime string `json:"start_time"`
	EndTime   string `json:"end_time"`
	ShowLogo  string `json:"show_logo"`
}

type FilterRule struct {
	OriginalName string
	OutputName   string
}

type LogEntry struct {
	Timestamp       string
	Channel         string
	TodayPrograms   int
	TomorrowPrograms int
	Status          string
}

var logEntries []LogEntry
var logBuffer strings.Builder

func logMessage(msg string) {
	fmt.Println(msg)
	logBuffer.WriteString(msg + "\n")
}

func main() {
	logMessage("🚀 Starting EPG Parser...")
	logMessage(fmt.Sprintf("🕒 Script started at: %s", time.Now().Format("2006-01-02 15:04:05 MST")))

	// Load IST timezone
	ist, err := time.LoadLocation("Asia/Kolkata")
	if err != nil {
		logMessage(fmt.Sprintf("❌ Error loading IST timezone: %v", err))
		saveLog()
		return
	}

	// Get today and tomorrow in IST
	now := time.Now().In(ist)
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, ist)
	tomorrow := today.AddDate(0, 0, 1)

	logMessage(fmt.Sprintf("📅 Today (IST): %s", today.Format("2006-01-02")))
	logMessage(fmt.Sprintf("📅 Tomorrow (IST): %s", tomorrow.Format("2006-01-02")))

	// Download and parse EPG files
	logMessage("\n📥 Downloading Jio TV EPG...")
	jioTV, err := downloadAndParseEPG("https://avkb.short.gy/jioepg.xml.gz")
	if err != nil {
		logMessage(fmt.Sprintf("❌ Error downloading Jio TV EPG: %v", err))
		saveLog()
		return
	}
	logMessage(fmt.Sprintf("✅ Jio TV: %d channels, %d programmes", len(jioTV.Channels), len(jioTV.Programmes)))

	logMessage("\n📥 Downloading Tata Play EPG...")
	tataTV, err := downloadAndParseEPG("https://avkb.short.gy/tsepg.xml.gz")
	if err != nil {
		logMessage(fmt.Sprintf("❌ Error downloading Tata Play EPG: %v", err))
		saveLog()
		return
	}
	logMessage(fmt.Sprintf("✅ Tata Play: %d channels, %d programmes", len(tataTV.Channels), len(tataTV.Programmes)))

	// Create channel maps by ID and by normalized name
	logMessage("\n🔀 Building channel index...")
	jioChannelsByID := make(map[string]*Channel)
	jioChannelsByName := make(map[string]*Channel)
	for i := range jioTV.Channels {
		ch := &jioTV.Channels[i]
		jioChannelsByID[ch.ID] = ch
		jioChannelsByName[normalizeChannelName(ch.DisplayName)] = ch
	}

	tataChannelsByID := make(map[string]*Channel)
	tataChannelsByName := make(map[string]*Channel)
	for i := range tataTV.Channels {
		ch := &tataTV.Channels[i]
		tataChannelsByID[ch.ID] = ch
		tataChannelsByName[normalizeChannelName(ch.DisplayName)] = ch
	}

	// Build programme maps by channel ID
	logMessage("🔀 Building programme index...")
	jioProgrammesByChannel := make(map[string][]Programme)
	for _, prog := range jioTV.Programmes {
		jioProgrammesByChannel[prog.Channel] = append(jioProgrammesByChannel[prog.Channel], prog)
	}

	tataProgrammesByChannel := make(map[string][]Programme)
	for _, prog := range tataTV.Programmes {
		tataProgrammesByChannel[prog.Channel] = append(tataProgrammesByChannel[prog.Channel], prog)
	}

	logMessage(fmt.Sprintf("✅ Indexed %d Jio channels and %d Tata channels", len(jioChannelsByName), len(tataChannelsByName)))

	// Load filter rules
	logMessage("\n📋 Loading filter.txt...")
	filterRules, err := loadFilterRules("filter.txt")
	if err != nil {
		logMessage(fmt.Sprintf("❌ Error loading filter.txt: %v", err))
		saveLog()
		return
	}
	logMessage(fmt.Sprintf("✅ Loaded %d filter rules", len(filterRules)))

	// Print all filter rules
	logMessage("\n📝 Filter Rules:")
	for i, rule := range filterRules {
		logMessage(fmt.Sprintf("   %d. %s → %s", i+1, rule.OriginalName, rule.OutputName))
	}

	// Create output directories
	os.RemoveAll("output-today")
	os.RemoveAll("output-tomorrow")
	os.MkdirAll("output-today", 0755)
	os.MkdirAll("output-tomorrow", 0755)

	// Process channels
	logMessage("\n⚙️  Processing channels...")
	logMessage("=" + strings.Repeat("=", 80))
	
	processed := 0
	savedToday := 0
	savedTomorrow := 0
	skipped := 0

	for _, rule := range filterRules {
		processed++
		logEntry := LogEntry{
			Timestamp: time.Now().Format("15:04:05"),
			Channel:   rule.OriginalName,
			Status:    "Not Found",
		}

		// Try to find channel in Jio first, then Tata
		normalizedSearch := normalizeChannelName(rule.OriginalName)
		
		var channel *Channel
		var programmes []Programme
		var source string

		// Check Jio first
		if ch, exists := jioChannelsByName[normalizedSearch]; exists {
			channel = ch
			programmes = jioProgrammesByChannel[ch.ID]
			source = "Jio"
		} else if ch, exists := tataChannelsByName[normalizedSearch]; exists {
			channel = ch
			programmes = tataProgrammesByChannel[ch.ID]
			source = "Tata"
		} else {
			// Try fuzzy matching
			channel, programmes, source = fuzzyFindChannel(rule.OriginalName, 
				jioChannelsByName, tataChannelsByName,
				jioProgrammesByChannel, tataProgrammesByChannel)
		}

		if channel == nil {
			logMessage(fmt.Sprintf("❌ Channel not found: %s", rule.OriginalName))
			logEntry.Status = "Not Found"
			logEntries = append(logEntries, logEntry)
			skipped++
			continue
		}

		logMessage(fmt.Sprintf("\n✅ Found: %s (from %s, ID: %s)", channel.DisplayName, source, channel.ID))
		logMessage(fmt.Sprintf("   Total programmes: %d", len(programmes)))

		// Filter and save today's schedule
		todayProgs := filterProgrammesByDateRange(programmes, today, ist)
		logMessage(fmt.Sprintf("   Today's programmes: %d", len(todayProgs)))
		logEntry.TodayPrograms = len(todayProgs)

		if len(todayProgs) > 0 {
			err := saveChannelJSON(channel, todayProgs, today, rule.OutputName, "output-today", ist)
			if err == nil {
				savedToday++
				logMessage(fmt.Sprintf("   ✅ Saved: output-today/%s", formatFilename(rule.OutputName)))
			} else {
				logMessage(fmt.Sprintf("   ❌ Error saving today: %v", err))
			}
		}

		// Filter and save tomorrow's schedule
		tomorrowProgs := filterProgrammesByDateRange(programmes, tomorrow, ist)
		logMessage(fmt.Sprintf("   Tomorrow's programmes: %d", len(tomorrowProgs)))
		logEntry.TomorrowPrograms = len(tomorrowProgs)

		if len(tomorrowProgs) > 0 {
			err := saveChannelJSON(channel, tomorrowProgs, tomorrow, rule.OutputName, "output-tomorrow", ist)
			if err == nil {
				savedTomorrow++
				logMessage(fmt.Sprintf("   ✅ Saved: output-tomorrow/%s", formatFilename(rule.OutputName)))
			} else {
				logMessage(fmt.Sprintf("   ❌ Error saving tomorrow: %v", err))
			}
		}

		if len(todayProgs) == 0 && len(tomorrowProgs) == 0 {
			logEntry.Status = "No Programmes"
			skipped++
		} else {
			logEntry.Status = "Success"
		}

		logEntries = append(logEntries, logEntry)
	}

	logMessage("\n" + strings.Repeat("=", 80))
	logMessage("\n📊 Final Summary:")
	logMessage(fmt.Sprintf("   Total Processed: %d channels", processed))
	logMessage(fmt.Sprintf("   ✅ Saved Today: %d", savedToday))
	logMessage(fmt.Sprintf("   ✅ Saved Tomorrow: %d", savedTomorrow))
	logMessage(fmt.Sprintf("   ❌ Skipped: %d", skipped))
	logMessage(fmt.Sprintf("\n🕒 Script completed at: %s", time.Now().Format("2006-01-02 15:04:05 MST")))

	// Save detailed log
	saveLog()
	saveDetailedLog()
	logMessage("\n✅ Done! Check epg-parser.log for details.")
}

func downloadAndParseEPG(url string) (*TV, error) {
	resp, err := http.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	gzReader, err := gzip.NewReader(resp.Body)
	if err != nil {
		return nil, err
	}
	defer gzReader.Close()

	var tv TV
	decoder := xml.NewDecoder(gzReader)
	err = decoder.Decode(&tv)
	if err != nil {
		return nil, err
	}

	return &tv, nil
}

func normalizeChannelName(name string) string {
	// Remove .json extension
	name = strings.TrimSuffix(name, ".json")
	
	// Convert to lowercase
	name = strings.ToLower(name)
	
	// Remove all spaces, dashes, and special characters
	reg := regexp.MustCompile(`[^a-z0-9]`)
	name = reg.ReplaceAllString(name, "")
	
	return name
}

func fuzzyFindChannel(searchName string, jioChannels, tataChannels map[string]*Channel,
	jioProgrammes, tataProgrammes map[string][]Programme) (*Channel, []Programme, string) {
	
	normalized := normalizeChannelName(searchName)
	
	// Try partial matching in Jio
	for key, ch := range jioChannels {
		if strings.Contains(key, normalized) || strings.Contains(normalized, key) {
			return ch, jioProgrammes[ch.ID], "Jio"
		}
	}
	
	// Try partial matching in Tata
	for key, ch := range tataChannels {
		if strings.Contains(key, normalized) || strings.Contains(normalized, key) {
			return ch, tataProgrammes[ch.ID], "Tata"
		}
	}
	
	return nil, nil, ""
}

func loadFilterRules(filename string) ([]FilterRule, error) {
	data, err := os.ReadFile(filename)
	if err != nil {
		return nil, err
	}

	lines := strings.Split(string(data), "\n")
	rules := make([]FilterRule, 0)

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		var rule FilterRule
		if strings.Contains(line, "=") {
			parts := strings.SplitN(line, "=", 2)
			rule.OriginalName = strings.TrimSpace(parts[0])
			rule.OutputName = strings.TrimSpace(parts[1])
		} else {
			rule.OriginalName = line
			rule.OutputName = line
		}

		rules = append(rules, rule)
	}

	return rules, nil
}

func filterProgrammesByDateRange(programmes []Programme, targetDate time.Time, loc *time.Location) []Programme {
	result := make([]Programme, 0)
	startOfDay := targetDate
	endOfDay := targetDate.AddDate(0, 0, 1).Add(-time.Nanosecond)

	for _, prog := range programmes {
		startTime, err := parseEPGTime(prog.Start, loc)
		if err != nil {
			continue
		}

		// Include programme if it starts within the target day OR if it's ongoing during the day
		endTime, err := parseEPGTime(prog.Stop, loc)
		if err != nil {
			continue
		}

		// Programme overlaps with target day if:
		// - It starts before end of day AND ends after start of day
		if startTime.Before(endOfDay) && endTime.After(startOfDay) {
			result = append(result, prog)
		}
	}

	// Sort by start time
	sort.Slice(result, func(i, j int) bool {
		t1, _ := parseEPGTime(result[i].Start, loc)
		t2, _ := parseEPGTime(result[j].Start, loc)
		return t1.Before(t2)
	})

	return result
}

func parseEPGTime(timeStr string, loc *time.Location) (time.Time, error) {
	// Format: "20251102183000 +0000" or "20251102183000"
	parts := strings.Fields(timeStr)
	if len(parts) == 0 {
		return time.Time{}, fmt.Errorf("invalid time format")
	}

	// Parse the timestamp part (first 14 characters: YYYYMMDDHHmmss)
	timestamp := parts[0]
	if len(timestamp) < 14 {
		return time.Time{}, fmt.Errorf("timestamp too short")
	}

	t, err := time.Parse("20060102150405", timestamp)
	if err != nil {
		return time.Time{}, err
	}

	// Convert from UTC to IST
	return t.UTC().In(loc), nil
}

func formatTime12Hour(t time.Time) string {
	hour := t.Hour()
	minute := t.Minute()
	period := "AM"
	
	if hour >= 12 {
		period = "PM"
		if hour > 12 {
			hour -= 12
		}
	}
	if hour == 0 {
		hour = 12
	}
	
	return fmt.Sprintf("%02d:%02d %s", hour, minute, period)
}

func formatFilename(name string) string {
	filename := strings.ToLower(name)
	filename = strings.ReplaceAll(filename, " ", "-")
	if !strings.HasSuffix(filename, ".json") {
		filename += ".json"
	}
	return filename
}

func saveChannelJSON(channel *Channel, programmes []Programme, date time.Time, outputName string, dir string, loc *time.Location) error {
	if len(programmes) == 0 {
		return nil
	}

	// Prepare JSON structure
	channelJSON := ChannelJSON{
		ChannelName: channel.DisplayName,
		ChannelLogo: channel.Icon.Src,
		Date:        date.Format("2006-01-02"),
		Programs:    make([]ProgramJSON, 0),
	}

	for _, prog := range programmes {
		startTime, err := parseEPGTime(prog.Start, loc)
		if err != nil {
			continue
		}
		endTime, err := parseEPGTime(prog.Stop, loc)
		if err != nil {
			continue
		}

		programJSON := ProgramJSON{
			ShowName:  prog.Title,
			StartTime: formatTime12Hour(startTime),
			EndTime:   formatTime12Hour(endTime),
			ShowLogo:  prog.Icon.Src,
		}
		channelJSON.Programs = append(channelJSON.Programs, programJSON)
	}

	// Generate filename
	filename := formatFilename(outputName)

	// Write JSON file
	filePath := filepath.Join(dir, filename)
	jsonData, err := json.MarshalIndent(channelJSON, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(filePath, jsonData, 0644)
}

func saveLog() {
	logFile := "epg-parser.log"
	err := os.WriteFile(logFile, []byte(logBuffer.String()), 0644)
	if err != nil {
		fmt.Printf("❌ Error saving log: %v\n", err)
	}
}

func saveDetailedLog() {
	var detailedLog strings.Builder
	
	detailedLog.WriteString("=" + strings.Repeat("=", 80) + "\n")
	detailedLog.WriteString("EPG PARSER - DETAILED EXECUTION LOG\n")
	detailedLog.WriteString("=" + strings.Repeat("=", 80) + "\n\n")
	detailedLog.WriteString(fmt.Sprintf("Execution Time: %s\n\n", time.Now().Format("2006-01-02 15:04:05 MST")))
	
	detailedLog.WriteString("CHANNEL PROCESSING DETAILS:\n")
	detailedLog.WriteString(strings.Repeat("-", 80) + "\n")
	detailedLog.WriteString(fmt.Sprintf("%-5s %-30s %-10s %-10s %-15s\n", "No.", "Channel", "Today", "Tomorrow", "Status"))
	detailedLog.WriteString(strings.Repeat("-", 80) + "\n")
	
	for i, entry := range logEntries {
		detailedLog.WriteString(fmt.Sprintf("%-5d %-30s %-10d %-10d %-15s\n", 
			i+1, 
			truncate(entry.Channel, 30), 
			entry.TodayPrograms, 
			entry.TomorrowPrograms, 
			entry.Status))
	}
	
	detailedLog.WriteString(strings.Repeat("=", 80) + "\n")
	
	err := os.WriteFile("epg-parser-detailed.log", []byte(detailedLog.String()), 0644)
	if err != nil {
		fmt.Printf("❌ Error saving detailed log: %v\n", err)
	}
}

func truncate(s string, max int) string {
	if len(s) > max {
		return s[:max-3] + "..."
	}
	return s
}
