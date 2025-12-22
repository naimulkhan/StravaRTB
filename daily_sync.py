import os
import json
import time
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIG ---
# Load from Environment Variables (set these in GitHub Secrets)
STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
GCP_JSON = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
SHEET_NAME = os.environ["SHEET_NAME"]

SEGMENT_IDS = [12345, 67890] # REPLACE WITH YOUR 7 IDS

def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(GCP_JSON, scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1

def refresh_token(token):
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': STRAVA_CLIENT_ID,
        'client_secret': STRAVA_CLIENT_SECRET,
        'refresh_token': token,
        'grant_type': 'refresh_token'
    })
    if res.status_code == 200:
        return res.json()['access_token']
    return None

def sync():
    sheet = get_sheet()
    rows = sheet.get_all_records()
    
    # Locate column indices (1-based)
    # structure: athlete_id, name, refresh_token, total_count, last_activity_epoch
    
    print(f"Starting sync for {len(rows)} runners...")
    
    for i, row in enumerate(rows):
        print(f"Syncing {row['name']}...")
        access_token = refresh_token(row['refresh_token'])
        
        if not access_token:
            print(f" -> Failed to auth {row['name']}")
            continue
            
        # Incremental Sync: Only fetch activities AFTER the last checked time
        last_epoch = row['last_activity_epoch']
        
        headers = {'Authorization': f"Bearer {access_token}"}
        # Fetch up to 50 new runs (should cover 24h easily)
        activities_res = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers=headers,
            params={'after': last_epoch, 'per_page': 50}
        )
        
        if activities_res.status_code == 200:
            new_activities = activities_res.json()
            new_segment_count = 0
            latest_run_time = last_epoch
            
            for act in new_activities:
                # Update our bookmark to the latest run found
                run_time = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
                if run_time > latest_run_time:
                    latest_run_time = int(run_time)

                # Fetch Details (Required for segment_efforts)
                # Sleep 1.5s to respect 100 req / 15 min limit
                time.sleep(1.5) 
                
                detail = requests.get(f"https://www.strava.com/api/v3/activities/{act['id']}", headers=headers).json()
                efforts = detail.get('segment_efforts', [])
                
                matches = [e for e in efforts if e['segment']['id'] in SEGMENT_IDS]
                if matches:
                    print(f"   -> Found {len(matches)} efforts in run {act['name']}")
                    new_segment_count += len(matches)

            # Update Sheet if we found new stuff
            if new_activities:
                # Row index is i + 2 (header + 0-index offset)
                current_total = row['total_count']
                sheet.update_cell(i + 2, 4, current_total + new_segment_count) # Update Total
                sheet.update_cell(i + 2, 5, latest_run_time) # Update Bookmark
        
        # Buffer between users
        time.sleep(1)

if __name__ == "__main__":
    from datetime import datetime
    sync()