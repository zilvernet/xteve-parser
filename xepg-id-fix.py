# xepg-id-fixer.py

import json
import os

# Path to the xepg.json file (adjust if needed; assuming it's in the current directory)
json_file_path = 'xepg.json'

# Check if the file exists
if not os.path.exists(json_file_path):
    print(f"Error: {json_file_path} not found in the current directory.")
    exit(1)

# Load the existing JSON data
with open(json_file_path, 'r') as f:
    data = json.load(f)

print(f"Loaded {len(data)} entries from {json_file_path}.")

# Step 1: Check for duplicate x-channelID values (user stated they are unique, but verify)
channel_ids = {}
duplicates = []
for old_key, channel in data.items():
    ch_id = channel.get('x-channelID')
    if ch_id is None or ch_id == '':
        print(f"Warning: Skipping entry '{old_key}' - missing or empty 'x-channelID'.")
        continue
    if ch_id in channel_ids:
        duplicates.append((ch_id, old_key, channel_ids[ch_id]))
    else:
        channel_ids[ch_id] = old_key

if duplicates:
    print("Error: Found duplicate x-channelID values. Cannot proceed safely.")
    for ch_id, new_key, old_key in duplicates:
        print(f"  Duplicate '{ch_id}': old keys '{old_key}' and '{new_key}'.")
    exit(1)
else:
    print("All x-channelID values are unique. Proceeding...")

# Step 2: Create a new dictionary with keys as x-channelID and count potential changes
new_data = {}
skipped = 0
rename_count = 0
epg_update_count = 0
for old_key, channel in data.items():
    ch_id = channel.get('x-channelID')
    if ch_id is None or ch_id == '':
        skipped += 1
        continue
    
    # Check for changes
    original_epg = channel.get('x-epg')
    if original_epg != ch_id:
        epg_update_count += 1
    if old_key != ch_id:
        rename_count += 1
    
    # Update the x-epg field to match x-channelID
    channel['x-epg'] = ch_id
    
    # Set the new key to x-channelID
    new_data[ch_id] = channel

print(f"Processed {len(new_data)} entries (skipped {skipped} entries without valid x-channelID).")

# Output console with changes done and count
print(f"\nChanges detected:")
print(f"  - {rename_count} keys renamed to match x-channelID")
print(f"  - {epg_update_count} x-epg fields updated to match x-channelID")
total_changes = rename_count + epg_update_count
if total_changes > 0:
    print(f"\nTotal changes: {total_changes}")
    # Step 3: Write the updated data back to the file only if there are changes
    with open(json_file_path, 'w') as f:
        json.dump(new_data, f, indent=2)
    print(f"Successfully updated {json_file_path}. All 'x-epg' fields now match their respective 'x-channelID' values, and JSON keys have been renamed where necessary.")
else:
    print(f"\nNo changes detected. Skipping write to {json_file_path}.")

# Optional: Backup the original file (uncomment if desired)
# original_backup = json_file_path + '.backup'
# with open(original_backup, 'w') as f:
#     json.dump(data, f, indent=2)
# print(f"Original data backed up to {original_backup}.")
