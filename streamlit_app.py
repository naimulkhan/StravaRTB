import streamlit as st
import pandas as pd
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time

# --- CONFIGURATION ---
CHALLENGE_START_DATE = datetime(2025, 12, 17) # Set to before your runs

# UPDATE THIS: Map ID to Name
SEGMENTS = {
    22655740: "Five Finger Hills",
    40409507: " Lakeshore Coxwell-Leslie",
    8223506:  "Pool to boardwalk",
    3219147:  "Scarborough Road",
    40410183: "Rainsford Rd",
    1705023:  "Stairway to Heaven",
    24820256: "Waterworks"
}

SEGMENT_IDS = list(SEGMENTS.keys()) # Helper list for checking IDs

# --- SETUP ---
def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    client = gspread.authorize(creds)
    sheet = client.open(st.secrets["google"]["sheet_name"]).sheet1
    return sheet

def init_db(sheet):
    """Creates headers if the sheet is empty."""
    if not sheet.row_values(1):
        # Static Headers + Dynamic Segment Names
        headers = ["athlete_id", "name", "refresh_token", "last_synced", "total_count"] + list(SEGMENTS.values())
        sheet.append_row(headers)

def exchange_code(code):
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': st.secrets["strava"]["client_id"],
        'client_secret': st.secrets["strava"]["client_secret"],
        'code': code,
        'grant_type': 'authorization_code'
    })
    return res.json()

def initial_backfill(access_token):
    """Counts efforts per segment individually."""
    headers = {'Authorization': f"Bearer {access_token}"}
    start_epoch = int(CHALLENGE_START_DATE.timestamp())
    
    # Init counters for each segment (set to 0)
    counts = {seg_id: 0 for seg_id in SEGMENT_IDS}
    
    # Get Activities
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    params = {'after': start_epoch, 'per_page': 200}
    response = requests.get(activities_url, headers=headers, params=params)
    
    if response.status_code != 200:
        return counts, start_epoch

    activities = response.json()
    latest_run_epoch = start_epoch
    
    my_bar = st.progress(0, text="Analyzing runs...")
    
    for i, act in enumerate(activities):
        run_time = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        if run_time > latest_run_epoch:
            latest_run_epoch = int(run_time)
            
        # Get Details
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers)
        
        if detail_res.status_code == 200:
            efforts = detail_res.json().get('segment_efforts', [])
            
            # Tally up matches
            for effort in efforts:
                sid = effort['segment']['id']
                if sid in counts:
                    counts[sid] += 1
                    
        if len(activities) > 0:
            my_bar.progress((i + 1) / len(activities))
            
    my_bar.empty()
    return counts, latest_run_epoch

# --- UI ---
st.set_page_config(page_title="Run Club Leaderboard", page_icon="🏃")
st.title("🏃 Segment Challenge Leaderboard")

sheet = get_sheet()
init_db(sheet) # Ensure headers exist

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
            records = sheet.get_all_records()
            df = pd.DataFrame(records)
            
            # Check if exists
            is_registered = False
            if not df.empty and 'athlete_id' in df.columns:
                 if ath['id'] in df['athlete_id'].values:
                     is_registered = True
            
            if is_registered:
                st.warning("You are already registered!")
            else:
                st.info("Scanning history... please wait.")
                counts, last_epoch = initial_backfill(data['access_token'])
                
                # Prepare Row: Meta Data + Total + Individual Segment Counts
                total = sum(counts.values())
                # Order matters: must match headers created in init_db
                segment_values = [counts[sid] for sid in SEGMENT_IDS]
                
                new_row = [
                    ath['id'], 
                    f"{ath['firstname']} {ath['lastname']}", 
                    data['refresh_token'], 
                    last_epoch,
                    total
                ] + segment_values
                
                sheet.append_row(new_row)
                st.balloons()
                st.success(f"Registered! Found {total} total efforts.")
                st.query_params.clear()

# --- DISPLAY LEADERBOARDS ---
data = sheet.get_all_records()

if data:
    df = pd.DataFrame(data)
    
    # Create Tabs: One for Overall, and one for each Segment
    tab_names = ["🏆 Overall"] + list(SEGMENTS.values())
    tabs = st.tabs(tab_names)
    
    # 1. Overall Tab
    with tabs[0]:
        if 'total_count' in df.columns:
            leaderboard = df[['name', 'total_count']].sort_values(by='total_count', ascending=False).reset_index(drop=True)
            st.dataframe(
                leaderboard, 
                column_config={
                    "name": "Runner", 
                    "total_count": st.column_config.NumberColumn("Total Efforts", format="%d ⚡")
                },
                use_container_width=True,
                hide_index=True
            )
            
    # 2. Segment Tabs
    # We iterate through the segment names to populate the other tabs
    for i, seg_name in enumerate(SEGMENTS.values()):
        with tabs[i + 1]: # +1 because index 0 is Overall
            if seg_name in df.columns:
                # Filter: Show only runners who have at least 1 effort on this segment
                seg_df = df[['name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
                
                # Highlight the Top 1 (The Segment Leader)
                st.dataframe(
                    seg_df,
                    column_config={
                        "name": "Runner",
                        seg_name: st.column_config.NumberColumn("Efforts", format="%d 🥇")
                    },
                    use_container_width=True,
                    hide_index=True
                )
else:
    st.info("No runners yet. Be the first to join!")

st.divider()
st.caption(f"Syncs automatically every 24h. Last update: {datetime.now().strftime('%H:%M UTC')}")