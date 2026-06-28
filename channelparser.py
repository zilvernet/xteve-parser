import configparser
import subprocess
import json
import os

# Version information
VERSION = "1.6"

# Load the configuration file
config_parser = configparser.ConfigParser()
config_parser.read('config.ini')

# Extract configuration variables from the config file
file_path = config_parser['Paths']['file_path']
guide_file_path = config_parser['Paths']['guide_file_path']
group_file_path = config_parser['Paths']['group_file_path']
json_file_path = config_parser['Paths']['json_file_path']
ignore_threshold = int(config_parser['Thresholds']['ignore_threshold'])
debug_mode = config_parser.getboolean('Debug', 'debug_mode')
guiderestore_debug = config_parser.getboolean('Debug', 'guiderestore_debug')
guidesave_debug = config_parser.getboolean('Debug', 'guidesave_debug')

# Counters
updated_records = 0
updated_groups = 0
updated_guide = 0
saved_guides_count = 0  # New counter for guides saved without xTeVe refresh

# Function to check if guide entry exists
def guide_entry_exists(channel):
    if os.path.exists(guide_file_path):
        with open(guide_file_path, 'r') as guide_file:
            lines = guide_file.readlines()
            for line in lines:
                if line.strip() == f"[{channel['x-channelID']}]":
                    return True
    return False

# Function to save guide entry
def save_guide_entry(channel):
    if not guide_entry_exists(channel):
        print(f"DEBUG: Attempting to save guide for channel {channel['x-channelID']} - {channel.get('name', 'Unnamed')}")
        with open(guide_file_path, 'a') as guide_file:
            guide_file.write(f"[{channel['x-channelID']}]\n")
            if channel.get('x-name') not in (None, ''):
                guide_file.write(f"x-name={channel['x-name']}\n")
            if channel.get('x-mapping') not in (None, ''):
                guide_file.write(f"x-mapping={channel['x-mapping']}\n")
            if channel.get('x-xmltv-file') not in (None, ''):
                guide_file.write(f"x-xmltv-file={channel['x-xmltv-file']}\n")
            if channel.get('tvg-logo') not in (None, ''):
                guide_file.write(f"tvg-logo={channel['tvg-logo']}\n")
            if channel.get('x-group-title') and channel.get('group-title') and channel['x-group-title'] != channel['group-title']:
                guide_file.write(f"x-group-title={channel['x-group-title']}\n")
            if channel.get('name'):
                guide_file.write(f"name={channel['name']}\n")
            guide_file.write("\n")
        return True
    return False

# Function to apply the restored guide to the channel
def apply_restored_guide(channel, restored_guide):
    fields_to_restore = ['x-mapping', 'x-xmltv-file', 'tvg-logo', 'x-name', 'x-group-title']
    restored = False
    for field in fields_to_restore:
        current_value = channel.get(field)
        restored_value = restored_guide.get(field)
        if guiderestore_debug:
            print(f"DEBUG: Comparing '{field}' for channel {channel['x-channelID']}:")
            print(f"DEBUG: Current value = '{current_value}'")
            print(f"DEBUG: Restored value = '{restored_value}'")
        if restored_value in (None, ''):
            if guiderestore_debug:
                print(f"DEBUG: Restored value for '{field}' is None or empty. Skipping.")
            continue
        if current_value != restored_value:
            print(f"DEBUG: Restoring '{field}' for channel {channel['x-channelID']}' from '{current_value}' to '{restored_value}'")
            channel[field] = restored_value
            restored = True
        else:
            if guiderestore_debug:
                print(f"DEBUG: No change needed for '{field}', already has value '{current_value}'")
    return restored

# Function to restore guide entry from guide.ini
def restore_guide_entry(channel):
    restored_values = {}
    if os.path.exists(guide_file_path):
        with open(guide_file_path, 'r') as guide_file:
            lines = guide_file.readlines()
            found = False
            if guiderestore_debug:
                print(f"DEBUG: Attempting to restore guide for channel '{channel['name']}' with ID '{channel['x-channelID']}'...")
            for i, line in enumerate(lines):
                if line.strip() == f"[{channel['x-channelID']}]":
                    found = True
                    if guiderestore_debug:
                        print(f"DEBUG: Found guide entry for channel '{channel['name']}' with ID '{channel['x-channelID']}'.")
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith("[") or not lines[j].strip():
                            break
                        if "=" in lines[j]:
                            key, value = lines[j].strip().split("=", 1)
                            if guiderestore_debug:
                                print(f"DEBUG: Found guide value - {key.strip()} = {value.strip()}")
                            restored_values[key.strip()] = value.strip()
                    return restored_values
            if not found and guiderestore_debug:
                print(f"DEBUG: No guide found for channel '{channel['name']}' with ID '{channel['x-channelID']}'.")
    else:
        if guiderestore_debug:
            print("DEBUG: guide.ini file does not exist. No guides to restore.")
    return None

# Function to get all channel IDs from guide.ini
def get_guide_channel_ids():
    guide_channel_ids = []
    if os.path.exists(guide_file_path):
        with open(guide_file_path, 'r') as guide_file:
            lines = guide_file.readlines()
            for line in lines:
                if line.startswith('[') and line.endswith(']\n'):
                    channel_id = line.strip().strip('[]')
                    guide_channel_ids.append(channel_id)
    return guide_channel_ids

# Load the config for forced channels, ignore list, etc.
config = configparser.ConfigParser(strict=False, delimiters=('='))
with open(file_path, 'r') as f:
    config.read_file(f)
group_config = configparser.ConfigParser()
with open(group_file_path, 'r') as f:
    group_config.read_file(f)

# Load custom group remapping from custom_group.ini
custom_group_path = os.path.join(os.path.dirname(__file__), 'custom_group.ini')
custom_group_map = {}
if os.path.exists(custom_group_path):
    with open(custom_group_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") or not line or line.startswith("#") or line.startswith(";"):
                continue  # skip section headers and comments
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.split('#')[0].strip()  # remove inline comments
                custom_group_map[key] = value

with open(file_path, 'r') as f:
    config.read_file(f)

with open(group_file_path, 'r') as f:
    group_config.read_file(f)

# Load the XEPG JSON file (xepg.json)
with open(json_file_path, 'r') as f:
    data = json.load(f)

# Helper function to check if a channel is forced (assumed from file.ini [Channels])
def is_forced_channel(channel):
    matched_name = apply_match_rules(channel['name'])
    group = channel.get('x-group-title', '')
    key_with_group = f"{matched_name}::{group}"
    return key_with_group in config['Channels'] or matched_name in config['Channels']

# Step 1: Group Rename Logic (runs first)
for channel_id, channel in data.items():
    x_group_title = channel.get('x-group-title', '')
    if x_group_title in group_config['groups']:
        new_group_name = group_config['groups'][x_group_title]
        print(f"Renaming group {x_group_title} to {new_group_name} for channel {channel['name']}.")
        channel['x-group-title'] = new_group_name
        updated_groups += 1

def apply_match_rules(original_name):
    modified_name = original_name
    if 'Matchrules' in config:
        for key in config['Matchrules']:
            try:
                old, new = eval(config['Matchrules'][key])  # expects tuple syntax
                modified_name = modified_name.replace(old, new)
            except Exception as e:
                if debug_mode:
                    print(f"Failed to apply Matchrule {key}: {e}")
    return modified_name

# Step 2: First loop to handle forced channels (reassignment, conflict resolution, etc.)
for channel_id, channel in data.items():
    current_channel_number = int(channel['x-channelID'])

    # Apply match rules to channel name
    matched_name = apply_match_rules(channel['name'])

    # Try strict name::group match
    group = channel.get('x-group-title', '')
    key_with_group = f"{matched_name}::{group}"

    if debug_mode:
        print(f"DEBUG: Matching {matched_name} with group {group} -> key: {key_with_group}")

    if key_with_group in config['Channels']:
        forced_number = int(config['Channels'][key_with_group])
    elif matched_name in config['Channels']:
        forced_number = int(config['Channels'][matched_name])
    else:
        continue  # Skip if no match

    # Skip if already at forced number
    if current_channel_number == forced_number:
        if debug_mode:
            print(f"Skipping forced channel {channel['name']} (already has number {forced_number})")
        continue

    print(f"Processing forced channel: {channel['name']} (current number: {current_channel_number}, forced number: {forced_number})")

    conflicting_channel = next((ch for ch in data.values() if int(ch['x-channelID']) == forced_number), None)
    if conflicting_channel:
        new_number = max([int(ch['x-channelID']) for ch in data.values()]) + 1
        print(f"Channel number {forced_number} is already taken by {conflicting_channel['name']}. Moving {conflicting_channel['name']} to {new_number}.")
        conflicting_channel['x-channelID'] = str(new_number)
        updated_records += 1

    channel['x-channelID'] = str(forced_number)
    updated_records += 1

# Step 3: Independent loop to handle `x-mapping` rule and guide saving for forced channels
for channel_id, channel in data.items():
    matched_name = apply_match_rules(channel['name'])
    group = channel.get('x-group-title', '')
    key_with_group = f"{matched_name}::{group}"

    is_forced = key_with_group in config['Channels'] or matched_name in config['Channels']

    if is_forced:
        # If x-mapping is "-", apply special forced channel rule
        if channel['x-mapping'] == "-":
            print(f"Applying forced rule to channel {channel['name']} (ID: {channel['x-channelID']})")
            channel['x-mapping'] = "120_Minutes"
            channel['x-xmltv-file'] = "xTeVe Dummy"
            channel['x-active'] = True
            updated_records += 1

        # Guide save logic for forced channels with guide2go mapping or specific logo URLs
        if channel['x-mapping'].startswith('guide2go'):
            if guide_entry_exists(channel):
                if guidesave_debug:
                    print(f"Guide entry for channel {channel['x-channelID']} already exists. Skipping guide save.")
            else:
                if guidesave_debug:
                    print(f"Saving guide for channel {channel['x-channelID']} (due to guide2go mapping)...")
                save_guide_entry(channel)
                saved_guides_count += 1  # Increment saved guide counter
                continue  # Skip the logo check if guide2go is matched

        # Additional check for specific logo URLs
        logo_url = channel.get('tvg-logo', '')
        if logo_url.startswith('https://i.imgur.com') or logo_url.startswith('https://upload.wikimedia.org') or logo_url.startswith('http://vidg.tmsimg.com'):
            if guide_entry_exists(channel):
                if guidesave_debug:
                    print(f"Guide entry for channel {channel['x-channelID']} already exists. Skipping guide save.")
            else:
                if guidesave_debug:
                    print(f"Saving guide for channel {channel['x-channelID']} (due to logo URL)...")
                save_guide_entry(channel)
                saved_guides_count += 1  # Increment saved guide counter

        # Additional forced save if x-description is not null
        if channel.get('x-description') not in (None, ''):
            if guide_entry_exists(channel):
                if guidesave_debug:
                    print(f"Guide entry for channel {channel['x-channelID']} already exists. Skipping guide save.")
            else:
                if guidesave_debug:
                    print(f"Saving guide for channel {channel['x-channelID']} (due to x-description)...")
                save_guide_entry(channel)
                saved_guides_count += 1  # Increment saved guide counter

# Step 4: Restore guide entries only for channels in guide.ini
guide_channel_ids = get_guide_channel_ids()

for guide_channel_id in guide_channel_ids:
    channel = next((ch for ch in data.values() if ch['x-channelID'] == guide_channel_id), None)
    if channel:
        # Restore guide even if the channel is inactive, then activate it
        if not channel.get('x-active', False):
            if guiderestore_debug:
                print(f"DEBUG: Channel {channel['name']} with ID {channel['x-channelID']} is inactive. Restoring and activating the channel.")
            channel['x-active'] = True  # Activate the channel after restoration

        restored_guide = restore_guide_entry(channel)
        if restored_guide:
            restored = apply_restored_guide(channel, restored_guide)  # Apply the restored guide
            if restored:
                updated_guide += 1
            else:
                if guiderestore_debug:
                    print(f"DEBUG: No changes were made to channel {channel['x-channelID']}.")
        else:
            if guiderestore_debug:
                print(f"DEBUG: No guide found to restore for channel {guide_channel_id}.")

# Step 5: Process ignored channels (without affecting forced channels)
for channel_id, channel in data.items():
    current_channel_number = int(channel['x-channelID'])

    matched_name = apply_match_rules(channel['name'])
    group = channel.get('x-group-title', '')
    key_with_group = f"{matched_name}::{group}"

    # Skip forced channels
    if key_with_group in config['Channels'] or matched_name in config['Channels']:
        continue

    # Process ignored channels
    if any(ignored_key in matched_name for ignored_key in config['Ignore'].values()):
        # Skip channels already above ignore_threshold
        if current_channel_number >= ignore_threshold:
            if debug_mode:
                print(f"Channel {channel['name']} already above {ignore_threshold}, skipping.")
            continue

        # Assign new channel number in the 190000 range
        new_number = max(
            [int(ch['x-channelID']) for ch in data.values() if int(ch['x-channelID']) >= ignore_threshold],
            default=ignore_threshold
        ) + 1

        print(f"Ignoring channel: {channel['name']}. Assigning new channel number {new_number}.")
        channel['x-channelID'] = str(new_number)
        channel['x-active'] = False
        updated_records += 1

# Step 6: Group Start Logic (Only for non-forced channels below 190000)
for channel_id, channel in data.items():
    current_channel_number = int(channel['x-channelID'])
    x_group_title = channel.get('x-group-title', '')

    # Skip forced channels and channels above 190000
    if current_channel_number > 190000 or channel['name'] in config['Channels']:
        continue

    # Check if there's a group start rule for this group
    if x_group_title in group_config['groupstart']:
        group_start_number = int(group_config['groupstart'][x_group_title])

        # If the channel number is below the group start, reassign it
        if current_channel_number < group_start_number:
            print(f"Assigned new group-based channel number {group_start_number} to {channel['name']}.")
            channel['x-channelID'] = str(group_start_number)
            group_config['groupstart'][x_group_title] = str(group_start_number + 1)
            updated_records += 1

# Step 7: Apply PPV-specific settings
for channel_id, channel in data.items():
    if channel.get('x-group-title', '') == "PPV":
        already_set = (
            channel.get('x-mapping') == "120_Minutes" and
            channel.get('x-xmltv-file') == "xTeVe Dummy" and
            channel.get('x-active') is True and
            channel.get('tvg-logo') == "https://archive.org/download/ppv_20200411/ppv.png"
        )
        if already_set:
            continue  # Skip if already set

        print(f"Applying PPV settings to channel {channel['name']} (ID: {channel['x-channelID']})")
        
        channel['x-mapping'] = "120_Minutes"
        channel['x-xmltv-file'] = "xTeVe Dummy"
        channel['x-active'] = True
        channel['tvg-logo'] = "https://archive.org/download/ppv_20200411/ppv.png"
        
        updated_records += 1

# Step 8: Refresh M3U sources every 3 hours (with error handling)
import time
current_hour = time.localtime().tm_hour
if current_hour % 3 == 0:  # Executes only every 3 hours
    print("Refreshing M3U sources...")
    try:
        subprocess.call(['/usr/bin/python3', 'xteve-refresh-m3u.py'])
    except FileNotFoundError:
        print("WARNING: xteve-refresh-m3u.py not found, skipping M3U refresh.")

# Step 9: Apply custom group renaming based on channel ID
for channel_id, channel in data.items():
    chan_id = channel['x-channelID']
    if chan_id in custom_group_map:
        new_group = custom_group_map[chan_id]
        if channel.get('x-group-title') != new_group:
            print(f"Reassigning group for channel {channel['name']} (ID: {chan_id}) to '{new_group}'")
            channel['x-group-title'] = new_group
            updated_groups += 1

# Step 10: Run xepg-id-fix.py before final counts
print("Running xepg-id-fix.py...")
subprocess.call(['/usr/bin/python3', 'xepg-id-fix.py'])

# Write the updated JSON data back to the file if any updates were made
if updated_records > 0 or updated_groups > 0 or updated_guide > 0:
    print(f"DEBUG: Writing updated JSON to {json_file_path}")
    with open(json_file_path, 'w') as f:
        json.dump(data, f, indent=2)

    # Refresh XEPG if any updates were made (records, groups, or guides)
    print("Refreshing XEPG...")
    subprocess.call(['/usr/bin/python3', 'xteve-refresh.py'])

# Display the count of guides saved without triggering xTeVe refresh
print(f"\n{saved_guides_count} guides were saved (no xTeVe refresh).")

# Display the final results, even if 0
print(f"\nResults:")
print(f"{updated_records} records were updated.")
print(f"{updated_groups} groups were updated.")
print(f"{updated_guide} guides were restored.")

# Display version information
print(f"\nScript Version: {VERSION}")

# Version 1.6 Notes:
# - Added execution of xepg-id-fix.py before final counts

# Version 1.5 Notes:
# - Fixed guide_entry_exists to correctly check for exact section headers [channelID] instead of substring matches.
# - Added error handling for missing xteve-refresh-m3u.py script.
# - Removed specific debugging for Bloomberg TV and Animal Planet West to improve performance.
# - Ensured guide saving occurs for channels meeting x-mapping, logo, or x-description conditions.