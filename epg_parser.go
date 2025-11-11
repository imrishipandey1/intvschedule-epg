package main

import (
    "compress/gzip"
    "encoding/json"
    "encoding/xml"
    "fmt"
    "log"
    "net/http"
    "os"
    "path/filepath"
    "strings"
    "time"
)

type TV struct {
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
	Category string `xml:"category"`
	Date    string `xml:"date"`
	Icon    Icon   `xml:"icon"`
}

type Icon struct {
	Src string `xml:"src,attr"`
}

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

// Enhanced filter rule
type FilterRule struct {
	OriginalName string
	OutputName   string
}

// Logging
var logLines []string

func main() {
	logLines = append(logLines, "========= EPG RUN LOG =========")
	ist, _ := time.LoadLocation("Asia/Kolkata")
	nowIST := time.Now().In(ist)
	today := time.Date(nowIST.Year(), nowIST.Month(), nowIST.Day(), 0, 0, 0, 0, ist)
	tomorrow := today.AddDate(0, 0, 1)

	logLines = append(logLines, fmt.Sprintf("Run Time (IST): %s", nowIST.Format(time.RFC3339)))
	logLines = append(logLines, fmt.Sprintf("Today (IST): %s | Tomorrow (IST): %s", today.Format("2006-01-02"), tomorrow.Format("2006-01-02")))

	// Download EPG files
	jioTV, err := downloadAndParseEPG("https://avkb.short.gy/jioepg.xml.gz")
	if err != nil {
		log.Fatalf("Download error for Jio TV EPG: %v", err)
	}
	logLines = append(logLines, fmt.Sprintf("JioTV: %d channels, %d programmes", len(jioTV.Channels), len(jioTV.Programmes)))

	tataPlay, err := downloadAndParseEPG("https://avkb.short.gy/tsepg.xml.gz")
	if err != nil {
		log.Fatalf("Download error for Tata Play EPG: %v", err)
	}
	logLines = append(logLines, fmt.Sprintf("TataPlay: %d channels, %d programmes", len(tataPlay.Channels), len(tataPlay.Programmes)))

	// Channel mapping (priority: Jio > Tata)
	channelMap := make(map[string]ChannelInfo)
	for _, ch := range tataPlay.Channels {
		channelMap[ch.ID] = ChannelInfo{ch.DisplayName, ch.Icon.Src}
	}
	for _, ch := range jioTV.Channels {
		channelMap[ch.ID] = ChannelInfo{ch.DisplayName, ch.Icon.Src}
	}

	// Programme de-duplication and merging
	progsByChannel := make(map[string][]Programme)
	for _, prog := range tataPlay.Programmes {
		progsByChannel[prog.Channel] = append(progsByChannel[prog.Channel], prog)
	}
	for _, prog := range jioTV.Programmes {
		progsByChannel[prog.Channel] = append(progsByChannel[prog.Channel], prog)
	}

	// Parse filter.txt rules
	filterRules, err := readFilterRules("filter.txt")
	if err != nil {
		log.Fatalf("Error reading filter.txt: %v", err)
	}
	logLines = append(logLines, fmt.Sprintf("Loaded %d filter rules", len(filterRules)))

	cleanDir("output-today")
	cleanDir("output-tomorrow")

	type SaveLog struct {
		Channel string
		OutputFile string
		NumPrograms int
		Date string
	}

	var logsToday []SaveLog
	var logsTomorrow []SaveLog
	processed := 0

	for _, rule := range filterRules {
		var matchedID string
		var matchedInfo ChannelInfo

		// Channel name autocorrection logic
		for id, info := range channelMap {
			if normalizeChannelName(info.Name) == normalizeChannelName(rule.OriginalName) {
				matchedID = id
				matchedInfo = info
				break
			}
		}

		if matchedID == "" {
			logLines = append(logLines, fmt.Sprintf("[SKIP] Channel not found for rule: %s", rule.OriginalName))
			continue
		}

		processed++
		progs := progsByChannel[matchedID]

		todayList := filterForDate(progs, today, ist)
		tomorrowList := filterForDate(progs, tomorrow, ist)

		if len(todayList) > 0 {
			out := ChannelOutput{
				ChannelName: matchedInfo.Name,
				ChannelLogo: matchedInfo.Logo,
				Date:        today.Format("2006-01-02"),
				Programs:    todayList,
			}
			filename := filepath.Join("output-today", rule.OutputName)
			saveJSON(filename, out)
			logsToday = append(logsToday, SaveLog{matchedInfo.Name, filename, len(todayList), out.Date})
		}

		if len(tomorrowList) > 0 {
			out := ChannelOutput{
				ChannelName: matchedInfo.Name,
				ChannelLogo: matchedInfo.Logo,
				Date:        tomorrow.Format("2006-01-02"),
				Programs:    tomorrowList,
			}
			filename := filepath.Join("output-tomorrow", rule.OutputName)
			saveJSON(filename, out)
			logsTomorrow = append(logsTomorrow, SaveLog{matchedInfo.Name, filename, len(tomorrowList), out.Date})
		}
	}

	// Print summary log
	logLines = append(logLines, fmt.Sprintf("Processed %d channels | Saved Today: %d | Saved Tomorrow: %d", processed, len(logsToday), len(logsTomorrow)))
	if len(logsToday) > 0 {
		logLines = append(logLines, "--- Today Output ---")
		for _, l := range logsToday {
			logLines = append(logLines, fmt.Sprintf("%s → %s (%d programs for %s)", l.Channel, l.OutputFile, l.NumPrograms, l.Date))
		}
	}
	if len(logsTomorrow) > 0 {
		logLines = append(logLines, "--- Tomorrow Output ---")
		for _, l := range logsTomorrow {
			logLines = append(logLines, fmt.Sprintf("%s → %s (%d programs for %s)", l.Channel, l.OutputFile, l.NumPrograms, l.Date))
		}
	}
	if len(logsToday) == 0 && len(logsTomorrow) == 0 {
		logLines = append(logLines, "No output generated. Check filter.txt and EPG source data.")
	}

	saveLogFile("epg_run.log", logLines)
}

// downloadAndParseEPG fetches and parses gzipped XMLTV data.
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
    err = xml.NewDecoder(gzReader).Decode(&tv)
    return &tv, err
}


// readFilterRules parses rules from filter.txt.
func readFilterRules(filename string) ([]FilterRule, error) {
	data, err := os.ReadFile(filename)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(string(data), "\n")
	var rules []FilterRule
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.Contains(line, "=") {
			parts := strings.SplitN(line, "=", 2)
			rules = append(rules, FilterRule{parseName(parts[0]), strings.TrimSpace(parts[1])})
		} else {
			name := parseName(line)
			rules = append(rules, FilterRule{name, fmt.Sprintf("%s.json", normalizeFilename(name))})
		}
	}
	return rules, nil
}
func parseName(s string) string {
	s = strings.TrimSpace(s)
	s = strings.TrimSuffix(s, ".json")
	s = strings.ReplaceAll(s, "-", " ")
	s = strings.ReplaceAll(s, "_", " ")
	return strings.Join(strings.Fields(s), " ")
}
func normalizeChannelName(s string) string {
	s = strings.ToLower(s)
	s = strings.ReplaceAll(s, " ", "")
	s = strings.ReplaceAll(s, "-", "")
	s = strings.ReplaceAll(s, "_", "")
	return s
}
func normalizeFilename(s string) string {
	return strings.ToLower(strings.ReplaceAll(strings.ReplaceAll(s, " ", "-"), "_", "-"))
}

// filterForDate filters and transforms EPG programmes within [00:00, 23:59] IST for a given day
func filterForDate(progs []Programme, day time.Time, ist *time.Location) []ProgramOutput {
	dayStart := day
	dayEnd := day.Add(24 * time.Hour)
	var result []ProgramOutput
	seen := make(map[string]bool)

	for _, prog := range progs {
		startT, err := parseEPGTime(prog.Start, ist)
		endT, err2 := parseEPGTime(prog.Stop, ist)
		if err != nil || err2 != nil {
			continue
		}
		// match program in range
		if (startT.Before(dayEnd) && endT.After(dayStart)) || (startT.Equal(dayStart) && endT.After(dayStart)) {
			key := fmt.Sprintf("%s_%s", startT.Format("15:04"), prog.Title)
			if seen[key] {
				continue
			}
			seen[key] = true
			showLogo := ""
			if prog.Icon.Src != "" {
				showLogo = prog.Icon.Src
			}
			result = append(result, ProgramOutput{
				ShowName:  prog.Title,
				StartTime: formatTime(startT),
				EndTime:   formatTime(endT),
				ShowLogo:  showLogo,
			})
		}
	}
	return result
}

// parseEPGTime interprets XMLTV timestamps and converts to IST.
func parseEPGTime(epgTime string, ist *time.Location) (time.Time, error) {
	parts := strings.Fields(epgTime)
	if len(parts) == 0 {
		return time.Time{}, fmt.Errorf("invalid epg time")
	}
	main := parts[0]
	layout := "20060102150405"
	t, err := time.Parse(layout, main)
	if err != nil {
		return time.Time{}, err
	}
	return t.In(ist), nil
}

func formatTime(t time.Time) string {
	hour := t.Hour()
	min := t.Minute()
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
	return fmt.Sprintf("%02d:%02d %s", hour, min, ampm)
}

func saveJSON(filename string, data interface{}) error {
	f, err := os.Create(filename)
	if err != nil {
		logLines = append(logLines, fmt.Sprintf("[ERROR] Could not save %s: %v", filename, err))
		return err
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	return enc.Encode(data)
}

func cleanDir(name string) {
	os.RemoveAll(name)
	os.MkdirAll(name, 0755)
}

func saveLogFile(filename string, lines []string) {
	f, err := os.Create(filename)
	if err != nil {
		fmt.Printf("Failed to write log: %v\n", err)
		return
	}
	defer f.Close()
	for _, l := range lines {
		_, _ = f.WriteString(l + "\n")
	}
}
