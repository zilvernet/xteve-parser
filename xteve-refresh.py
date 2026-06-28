import os
import requests
import sys

# Define xTeVe server details (override with environment variables)
XTEVE_IP = os.environ.get("XTEVE_IP", "192.0.2.20")    # xTeVe server IP
XTEVE_PORT = os.environ.get("XTEVE_PORT", "34400")     # xTeVe API port
XTEVE_API_URL = f"http://{XTEVE_IP}:{XTEVE_PORT}/api/"

# Function to make API requests to xTeVe
def send_api_request(command):
    url = XTEVE_API_URL
    headers = {"Content-Type": "application/json"}
    payload = {"cmd": command}

    # Send API request
    response = requests.post(url, json=payload, headers=headers)

    # Check if the request was successful
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        sys.exit(1)

# Main function to refresh XEPG
def refresh_xepg():
    print("Refreshing XEPG...")

    # Update XEPG
    response = send_api_request("update.xepg")
    if response.get("status"):
        print("XEPG updated successfully.")
    else:
        print("Failed to update XEPG.")

if __name__ == "__main__":
    refresh_xepg()
