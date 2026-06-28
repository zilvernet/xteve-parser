import os
import re
import json

# Paths to the log file and xepg.json file (override with environment variables)
LOG_FILE_PATH = os.environ.get("XTEVE_LOG_FILE", "/docker_data/xteve-mama/log/xteve.log")
XEPG_FILE_PATH = os.environ.get("XTEVE_XEPG_FILE", "/docker_data/xteve-mama/config/xepg.json")

# Regex patterns to match playlist, tuner, and channel information
TUNER_PATTERN = re.compile(r"Playlist:\s*(.*?)\s*-\s*Tuner:\s*(\d+)\s*/\s*(\d+)")
CHANNEL_PATTERN = re.compile(r"Channel Name:\s*(.*?)\s*$")
STREAMING_ENDED_PATTERN = re.compile(r"No client is using this channel anymore")

# Load xEPG data from xepg.json
def load_xepg_data():
    try:
        with open(XEPG_FILE_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: xEPG data file not found at {XEPG_FILE_PATH}")
        return None

# Match channel to tuner using xEPG data
def match_channel_to_tuner(channel_name, xepg_data):
    for channel_id, channel_info in xepg_data.items():
        if channel_info.get("x-name") == channel_name and channel_info.get("_file.m3u.name"):
            return channel_info["_file.m3u.name"], channel_info.get("x-channelID")
    return None, None

# Function to parse the log file for tuners and active channels
def parse_xteve_log(xepg_data):
    tuners_info = {}
    current_playlist = None

    try:
        with open(LOG_FILE_PATH, "r") as log_file:
            lines = log_file.readlines()

            for line in lines:
                # Check for tuner information by playlist
                tuner_match = TUNER_PATTERN.search(line)
                if tuner_match:
                    playlist = tuner_match.group(1).strip()
                    tuners_in_use = int(tuner_match.group(2))
                    total_tuners = int(tuner_match.group(3))

                    if playlist not in tuners_info:
                        tuners_info[playlist] = {
                            "tuners_in_use": tuners_in_use,
                            "total_tuners": total_tuners,
                            "channels": set()
                        }

                    tuners_info[playlist]["tuners_in_use"] = tuners_in_use
                    tuners_info[playlist]["total_tuners"] = total_tuners

                    # Clear the channel list when no tuners are in use
                    if tuners_in_use == 0:
                        tuners_info[playlist]["channels"].clear()

                    current_playlist = playlist

                # Check for channel information
                channel_match = CHANNEL_PATTERN.search(line)
                if channel_match:
                    channel_name = channel_match.group(1).strip()

                    # Use xEPG data to match the channel to the correct playlist (tuner)
                    matched_playlist, channel_number = match_channel_to_tuner(channel_name, xepg_data)

                    if matched_playlist and channel_number:
                        if matched_playlist not in tuners_info:
                            tuners_info[matched_playlist] = {
                                "tuners_in_use": 0,
                                "total_tuners": 0,
                                "channels": set()
                            }

                        # Store the channel as "number - name"
                        tuners_info[matched_playlist]["channels"].add(f"{channel_number} - {channel_name}")

                # Check if streaming has ended for a channel
                if STREAMING_ENDED_PATTERN.search(line) and current_playlist:
                    tuners_info[current_playlist]["channels"].clear()  # Clear channels if the stream ends

    except FileNotFoundError:
        print(f"Log file not found: {LOG_FILE_PATH}")
        return None

    return tuners_info

# Function to display tuner and channel status by playlist
def display_tuner_status():
    xepg_data = load_xepg_data()

    if not xepg_data:
        return

    tuners_info = parse_xteve_log(xepg_data)
    if tuners_info:
        print("Current tuner status:")
        for playlist, info in tuners_info.items():
            tuners_in_use = info["tuners_in_use"]
            total_tuners = info["total_tuners"]
            channels = info["channels"]

            print(f"Playlist: {playlist}, Tuners in use: {tuners_in_use} / {total_tuners}")
            if channels:
                print(f"  Channels being played:")
                for channel in channels:
                    print(f"    - {channel}")
            else:
                print(f"  No active channels.")
    else:
        print("No tuner information found.")

if __name__ == "__main__":
    display_tuner_status()
