package main

import (
	"compress/gzip"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
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

func main() {
	fmt.Println("🚀 Starting EPG Parser...")

	// Load IST timezone
	ist, err := time.LoadLocation("Asia/Kolkata")
	if err != nil {
		fmt.Printf("❌ Error loading IST timezone: %v\n", err)
		return
	}

	// Get today and tomorrow in IST
	now := time.Now().In(ist)
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, ist)
	tomorrow := today.AddDate(0, 0, 1)

	fmt.Printf("📅 Today: %s\n", today.Format("2006-01-02"))
	fmt.Printf("📅 Tomorrow: %s\n", tomorrow.Format("2006-01-02"))

	// Download and parse EPG files
	fmt.Println("\n📥 Downloading Jio TV EPG...")
	jioTV, err := downloadAndParseEPG("https://avkb.short.gy/jioepg.xml.gz")
	if err != nil {
		fmt.Printf("❌ Error downloading Jio TV EPG: %v\n", err)
		return
	}
	fmt.Printf("✅ Jio TV: %d channels, %d programmes\n", len(jioTV.Channels), len(jioTV.Programmes))

	fmt.Println("\n📥 Downloading Tata Play EPG...")
	tataTV, err := downloadAndParseEPG("https://avkb.short.gy/tsepg.xml.gz")
	if err != nil {
		fmt.Printf("❌ Error downloading Tata Play EPG: %v\n", err)
		return
	}
	fmt.Printf("✅ Tata Play: %d channels, %d programmes\n", len(tataTV.Channels), len(tataTV.Programmes))

	// Merge data (Jio priority)
	fmt.Println("\n🔀 Merging EPG data...")
	mergedChannels := mergeChannels(jioTV, tataTV)
	mergedProgrammes := mergeProgrammes(jioTV, tataTV)
	fmt.Printf("✅ Merged: %d channels, %d programmes\n", len(mergedChannels), len(mergedProgrammes))

	// Load filter rules
	fmt.Println("\n📋 Loading filter.txt...")
	filterRules, err := loadFilterRules("filter.txt")
	if err != nil {
		fmt.Printf("❌ Error loading filter.txt: %v\n", err)
		return
	}
	fmt.Printf("✅ Loaded %d filter rules\n", len(filterRules))

	// Create output directories
	os.RemoveAll("output-today")
	os.RemoveAll("output-tomorrow")
	os.MkdirAll("output-today", 0755)
	os.MkdirAll("output-tomorrow", 0755)

	// Process channels
	fmt.Println("\n⚙️  Processing channels...")
	processed := 0
	savedToday := 0
	savedTomorrow := 0
	skipped := 0

	for _, rule := range filterRules {
		processed++
		channel := findChannelByName(mergedChannels, rule.OriginalName)
		if channel == nil {
			skipped++
			continue
		}

		programmes := filterProgrammesByChannel(mergedProgrammes, channel.ID)

		// Generate today's schedule
		todayProgs := filterProgrammesByDate(programmes, today, ist)
		if len(todayProgs) > 0 {
			err := saveChannelJSON(channel, todayProgs, today, rule.OutputName, "output-today", ist)
			if err == nil {
				savedToday++
			}
		}

		// Generate tomorrow's schedule
		tomorrowProgs := filterProgrammesByDate(programmes, tomorrow, ist)
		if len(tomorrowProgs) > 0 {
			err := saveChannelJSON(channel, tomorrowProgs, tomorrow, rule.OutputName, "output-tomorrow", ist)
			if err == nil {
				savedTomorrow++
			}
		}

		if len(todayProgs) == 0 && len(tomorrowProgs) == 0 {
			skipped++
		}
	}

	fmt.Printf("\n📊 Summary:\n")
	fmt.Printf("   Processed: %d channels\n", processed)
	fmt.Printf("   Saved (today): %d\n", savedToday)
	fmt.Printf("   Saved (tomorrow): %d\n", savedTomorrow)
	fmt.Printf("   Skipped: %d\n", skipped)
	fmt.Println("\n✅ Done!")
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

func mergeChannels(jio, tata *TV) map[string]*Channel {
	channels := make(map[string]*Channel)

	// Add Jio channels first (priority)
	for i := range jio.Channels {
		ch := &jio.Channels[i]
		key := normalizeChannelName(ch.DisplayName)
		channels[key] = ch
	}

	// Add Tata channels if not exist
	for i := range tata.Channels {
		ch := &tata.Channels[i]
		key := normalizeChannelName(ch.DisplayName)
		if _, exists := channels[key]; !exists {
			channels[key] = ch
		}
	}

	return channels
}

func mergeProgrammes(jio, tata *TV) []Programme {
	progMap := make(map[string]Programme)

	// Add Jio programmes first (priority)
	for _, prog := range jio.Programmes {
		key := fmt.Sprintf("%s_%s", prog.Channel, prog.Start)
		progMap[key] = prog
	}

	// Add Tata programmes if not exist
	for _, prog := range tata.Programmes {
		key := fmt.Sprintf("%s_%s", prog.Channel, prog.Start)
		if _, exists := progMap[key]; !exists {
			progMap[key] = prog
		}
	}

	// Convert map to slice
	programmes := make([]Programme, 0, len(progMap))
	for _, prog := range progMap {
		programmes = append(programmes, prog)
	}

	return programmes
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
		if line == "" {
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

func normalizeChannelName(name string) string {
	name = strings.ToLower(name)
	name = strings.ReplaceAll(name, " ", "")
	name = strings.ReplaceAll(name, "-", "")
	name = strings.ReplaceAll(name, ".json", "")
	return name
}

func findChannelByName(channels map[string]*Channel, searchName string) *Channel {
	normalized := normalizeChannelName(searchName)
	for key, ch := range channels {
		if key == normalized {
			return ch
		}
	}
	return nil
}

func filterProgrammesByChannel(programmes []Programme, channelID string) []Programme {
	result := make([]Programme, 0)
	for _, prog := range programmes {
		if prog.Channel == channelID {
			result = append(result, prog)
		}
	}
	return result
}

func filterProgrammesByDate(programmes []Programme, targetDate time.Time, loc *time.Location) []Programme {
	result := make([]Programme, 0)
	endOfDay := targetDate.AddDate(0, 0, 1).Add(-time.Second)

	for _, prog := range programmes {
		startTime, err := parseEPGTime(prog.Start, loc)
		if err != nil {
			continue
		}

		if (startTime.Equal(targetDate) || startTime.After(targetDate)) && startTime.Before(endOfDay) {
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
	// Format: "20251102183000 +0000"
	parts := strings.Split(timeStr, " ")
	if len(parts) < 1 {
		return time.Time{}, fmt.Errorf("invalid time format")
	}

	t, err := time.Parse("20060102150405", parts[0])
	if err != nil {
		return time.Time{}, err
	}

	// Convert to IST
	return t.In(loc), nil
}

func formatTime12Hour(t time.Time) string {
	return t.Format("03:04 PM")
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
	filename := strings.ToLower(outputName)
	filename = strings.ReplaceAll(filename, " ", "-")
	if !strings.HasSuffix(filename, ".json") {
		filename += ".json"
	}

	// Write JSON file
	filePath := filepath.Join(dir, filename)
	jsonData, err := json.MarshalIndent(channelJSON, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(filePath, jsonData, 0644)
}
