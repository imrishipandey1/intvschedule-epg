package main

import (
	"compress/gzip"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
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
	Start       string `xml:"start,attr"`
	Stop        string `xml:"stop,attr"`
	Channel     string `xml:"channel,attr"`
	Title       string `xml:"title"`
	Description string `xml:"desc"`
	Category    string `xml:"category"`
	Date        string `xml:"date"`
	Icon        Icon   `xml:"icon"`
}

type Icon struct {
	Src string `xml:"src,attr"`
}

// JSON output structures
type ChannelOutput struct {
	ChannelName string          `json:"channel_name"`
	ChannelLogo string          `json:"channel_logo"`
	Date        string          `json:"date"`
	Programs    []ProgramOutput `json:"programs"`
}

type ProgramOutput struct {
	ShowName  string `json:"show_name"`
	StartTime string `json:"start_time"`
	EndTime   string `json:"end_time"`
	ShowLogo  string `json:"show_logo"`
}

type ChannelInfo struct {
	Name string
	Logo string
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
		fmt.Printf("Error loading IST timezone: %v\n", err)
		return
	}

	// Get current time in IST
	nowIST := time.Now().In(ist)
	today := time.Date(nowIST.Year(), nowIST.Month(), nowIST.Day(), 0, 0, 0, 0, ist)
	tomorrow := today.AddDate(0, 0, 1)

	fmt.Printf("📅 Today (IST): %s\n", today.Format("2006-01-02"))
	fmt.Printf("📅 Tomorrow (IST): %s\n", tomorrow.Format("2006-01-02"))

	// Download and parse EPG files
	fmt.Println("\n📥 Downloading Jio TV EPG...")
	jioTV, err := downloadAndParseEPG("https://avkb.short.gy/jioepg.xml.gz")
	if err != nil {
		fmt.Printf("Error downloading Jio TV EPG: %v\n", err)
		return
	}
	fmt.Printf("✅ Jio TV: %d channels, %d programmes\n", len(jioTV.Channels), len(jioTV.Programmes))

	fmt.Println("\n📥 Downloading Tata Play EPG...")
	tataPlay, err := downloadAndParseEPG("https://avkb.short.gy/tsepg.xml.gz")
	if err != nil {
		fmt.Printf("Error downloading Tata Play EPG: %v\n", err)
		return
	}
	fmt.Printf("✅ Tata Play: %d channels, %d programmes\n", len(tataPlay.Channels), len(tataPlay.Programmes))

	// Merge channel data (Jio priority)
	channelMap := make(map[string]ChannelInfo)
	for _, ch := range tataPlay.Channels {
		channelMap[ch.ID] = ChannelInfo{
			Name: strings.TrimSpace(ch.DisplayName),
			Logo: ch.Icon.Src,
		}
	}
	for _, ch := range jioTV.Channels {
		channelMap[ch.ID] = ChannelInfo{
			Name: strings.TrimSpace(ch.DisplayName),
			Logo: ch.Icon.Src,
		}
	}

	// Merge programme data (Jio priority)
	programmesByChannel := make(map[string][]Programme)
	for _, prog := range tataPlay.Programmes {
		programmesByChannel[prog.Channel] = append(programmesByChannel[prog.Channel], prog)
	}
	for _, prog := range jioTV.Programmes {
		// Jio programmes override Tata
		if _, exists := programmesByChannel[prog.Channel]; !exists {
			programmesByChannel[prog.Channel] = []Programme{}
		}
		// For simplicity, we'll add all Jio programmes and let the filter handle duplicates
		programmesByChannel[prog.Channel] = append(programmesByChannel[prog.Channel], prog)
	}

	// Read filter.txt
	fmt.Println("\n📋 Reading filter.txt...")
	filterRules, err := readFilterRules("filter.txt")
	if err != nil {
		fmt.Printf("Error reading filter.txt: %v\n", err)
		return
	}
	fmt.Printf("✅ Loaded %d filter rules\n", len(filterRules))

	// Create output directories
	os.RemoveAll("output-today")
	os.RemoveAll("output-tomorrow")
	os.MkdirAll("output-today", 0755)
	os.MkdirAll("output-tomorrow", 0755)

	// Process each filter rule
	processed := 0
	savedToday := 0
	savedTomorrow := 0

	for _, rule := range filterRules {
		processed++

		// Find matching channel
		var matchedChannelID string
		var matchedChannelInfo ChannelInfo

		for id, info := range channelMap {
			if normalizeChannelName(info.Name) == normalizeChannelName(rule.OriginalName) {
				matchedChannelID = id
				matchedChannelInfo = info
				break
			}
		}

		if matchedChannelID == "" {
			continue
		}

		// Get programmes for this channel
		programmes := programmesByChannel[matchedChannelID]
		if len(programmes) == 0 {
			continue
		}

		// Filter and generate JSON for today
		todayPrograms := filterProgrammesForDate(programmes, today, ist)
		if len(todayPrograms) > 0 {
			output := ChannelOutput{
				ChannelName: matchedChannelInfo.Name,
				ChannelLogo: matchedChannelInfo.Logo,
				Date:        today.Format("2006-01-02"),
				Programs:    todayPrograms,
			}
			filename := filepath.Join("output-today", rule.OutputName)
			if err := saveJSON(filename, output); err == nil {
				savedToday++
			}
		}

		// Filter and generate JSON for tomorrow
		tomorrowPrograms := filterProgrammesForDate(programmes, tomorrow, ist)
		if len(tomorrowPrograms) > 0 {
			output := ChannelOutput{
				ChannelName: matchedChannelInfo.Name,
				ChannelLogo: matchedChannelInfo.Logo,
				Date:        tomorrow.Format("2006-01-02"),
				Programs:    tomorrowPrograms,
			}
			filename := filepath.Join("output-tomorrow", rule.OutputName)
			if err := saveJSON(filename, output); err == nil {
				savedTomorrow++
			}
		}
	}

	fmt.Printf("\n✨ Processed: %d channels | Saved Today: %d | Saved Tomorrow: %d\n", processed, savedToday, savedTomorrow)
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
	if err := decoder.Decode(&tv); err != nil {
		return nil, err
	}

	return &tv, nil
}

func readFilterRules(filename string) ([]FilterRule, error) {
	data, err := os.ReadFile(filename)
	if err != nil {
		return nil, err
	}

	lines := strings.Split(string(data), "\n")
	var rules []FilterRule

	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}

		var rule FilterRule
		if strings.Contains(line, "=") {
			parts := strings.SplitN(line, "=", 2)
			rule.OriginalName = parseChannelNameFromFilter(parts[0])
			rule.OutputName = strings.TrimSpace(parts[1])
		} else {
			rule.OriginalName = parseChannelNameFromFilter(line)
			rule.OutputName = normalizeFilename(rule.OriginalName) + ".json"
		}

		rules = append(rules, rule)
	}

	return rules, nil
}

func parseChannelNameFromFilter(s string) string {
	s = strings.TrimSpace(s)
	// Remove .json extension if present
	s = strings.TrimSuffix(s, ".json")
	// Replace dashes and underscores with spaces
	s = strings.ReplaceAll(s, "-", " ")
	s = strings.ReplaceAll(s, "_", " ")
	// Normalize spaces
	s = strings.Join(strings.Fields(s), " ")
	return s
}

func normalizeChannelName(s string) string {
	s = strings.ToLower(s)
	s = strings.ReplaceAll(s, " ", "")
	s = strings.ReplaceAll(s, "-", "")
	s = strings.ReplaceAll(s, "_", "")
	return s
}

func normalizeFilename(s string) string {
	s = strings.ToLower(s)
	s = strings.ReplaceAll(s, " ", "-")
	return s
}

func filterProgrammesForDate(programmes []Programme, targetDate time.Time, ist *time.Location) []ProgramOutput {
	var result []ProgramOutput
	seen := make(map[string]bool)

	dayStart := targetDate
	dayEnd := targetDate.Add(24 * time.Hour)

	for _, prog := range programmes {
		startTime, err := parseEPGTime(prog.Start, ist)
		if err != nil {
			continue
		}

		endTime, err := parseEPGTime(prog.Stop, ist)
		if err != nil {
			continue
		}

		// Check if programme is within the target date
		if startTime.Before(dayEnd) && endTime.After(dayStart) {
			// Dedup based on start time and title
			key := fmt.Sprintf("%s_%s", startTime.Format("15:04"), prog.Title)
			if seen[key] {
				continue
			}
			seen[key] = true

			result = append(result, ProgramOutput{
				ShowName:  prog.Title,
				StartTime: formatTime(startTime),
				EndTime:   formatTime(endTime),
				ShowLogo:  prog.Icon.Src,
			})
		}
	}

	return result
}

func parseEPGTime(epgTime string, ist *time.Location) (time.Time, error) {
	// Format: "20251102183000 +0000"
	parts := strings.Fields(epgTime)
	if len(parts) < 1 {
		return time.Time{}, fmt.Errorf("invalid time format")
	}

	// Parse the timestamp
	t, err := time.Parse("20060102150405", parts[0])
	if err != nil {
		return time.Time{}, err
	}

	// Convert UTC to IST
	return t.In(ist), nil
}

func formatTime(t time.Time) string {
	hour := t.Hour()
	minute := t.Minute()
	ampm := "AM"

	if hour >= 12 {
		ampm = "PM"
		if hour > 12 {
			hour -= 12
		}
	}
	if hour == 0 {
		hour = 12
	}

	return fmt.Sprintf("%02d:%02d %s", hour, minute, ampm)
}

func saveJSON(filename string, data interface{}) error {
	file, err := os.Create(filename)
	if err != nil {
		return err
	}
	defer file.Close()

	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	return encoder.Encode(data)
}

