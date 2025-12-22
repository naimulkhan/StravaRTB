import streamlit as st
import pandas as pd
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time

# --- CONFIG ---
CHALLENGE_START_DATE = datetime(2025, 12, 17) # SET THIS to your challenge start date
SCOPES = ['read', 'activity:read_all']

# REPLACE with your actual Segment IDs (Integers)
SEGMENT_IDS = [
    22655740, 
    40409507, 
    8223506,
    3219147,
    40410183,
    1705023,
    24820256,


    # ... add the rest here
]

# --- SETUP ---
def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    client = gspread.authorize(creds)
    return client.open(st.secrets["google"]["sheet_name"]).sheet1

def exchange_code(code):
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': st.secrets["strava"]["client_id"],
        'client_secret': st.secrets["strava"]["client_secret"],
        'code': code,
        'grant_type': 'authorization_code'
    })
    return res.json()

def initial_backfill(access_token):
    """Fetches all runs since challenge start and counts segments immediately."""
    headers = {'Authorization': f"Bearer {access_token}"}
    
    # 1. Get runs since challenge start
    start_epoch = int(CHALLENGE_START_DATE.timestamp())
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    
    # Fetch up to 200 items (covers ~90 days for most runners)
    params = {'after': start_epoch, 'per_page': 200}
    response = requests.get(activities_url, headers=headers, params=params)
    
    if response.status_code != 200:
        return 0, start_epoch

    activities = response.json()
    total_count = 0
    latest_run_epoch = start_epoch
    
    # Progress bar for the user
    my_bar = st.progress(0, text="Analyzing your past runs...")
    total_acts = len(activities)
    
    for i, act in enumerate(activities):
        # Update timestamp for future syncs to avoid double counting
        run_time = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        if run_time > latest_run_epoch:
            latest_run_epoch = int(run_time)
            
        # 2. Fetch Detailed Activity (Required to see segment efforts)
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers)
        
        if detail_res.status_code == 200:
            efforts = detail_res.json().get('segment_efforts', [])
            matches = [e for e in efforts if e['segment']['id'] in SEGMENT_IDS]
            total_count += len(matches)
            
        # Update progress bar
        if total_acts > 0:
            my_bar.progress((i + 1) / total_acts)
        
    my_bar.empty()
    return total_count, latest_run_epoch

# --- UI ---
st.title("🏃 Segment Challenge Leaderboard")

sheet = get_sheet()

# Sidebar: Join
with st.sidebar:
    st.header("Join Challenge")
    auth_url = (
        f"https://www.strava.com/oauth/authorize?client_id={st.secrets['strava']['client_id']}"
        f"&response_type=code&redirect_uri={st.secrets['strava']['redirect_uri']}"
        "&approval_prompt=force&scope=activity:read_all"
    )
    st.link_button("Connect Strava", auth_url)

    if "code" in st.query_params:
        code = st.query_params["code"]
        data = exchange_code(code)
        
        if "access_token" in data:
            ath = data['athlete']
            # Check if exists
            records = sheet.get_all_records()
            df = pd.DataFrame(records)
            
            # Check if athlete_id exists in the dataframe (handle empty sheet case)
            is_registered = False
            if not df.empty and 'athlete_id' in df.columns:
                 if ath['id'] in df['athlete_id'].values:
                     is_registered = True
            
            if is_registered:
                st.warning("You are already registered!")
            else:
                # Add new user with immediate backfill
                st.info("Success! Scanning your past runs... please wait.")
                
                # Run the backfill logic
                initial_count, last_epoch = initial_backfill(data['access_token'])
                
                # Append to Sheet
                sheet.append_row([
                    ath['id'], 
                    f"{ath['firstname']} {ath['lastname']}", 
                    data['refresh_token'], 
                    initial_count, 
                    last_epoch
                ])
                
                st.balloons()
                st.success(f"Registered! Found {initial_count} efforts so far.")
                st.query_params.clear() # Clear URL to prevent re-run on refresh

# Display Leaderboard
data = sheet.get_all_records()
if data:
    df = pd.DataFrame(data)
    # Sort by total_count desc
    if not df.empty and 'total_count' in df.columns:
        df = df.sort_values(by='total_count', ascending=False).reset_index(drop=True)
        st.dataframe(
            df[['name', 'total_count']],
            column_config={
                "name": "Runner", 
                "total_count": st.column_config.NumberColumn("Efforts", format="%d ⚡")
            },
            use_container_width=True
        )
    else:
        st.info("No data yet.")
        
    st.caption(f"Syncs automatically every 24h. Last system update: {datetime.now().strftime('%H:%M UTC')}")
else:
    st.info("No runners registered yet. Use the sidebar to join!")