import streamlit as st
import pandas as pd
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import random

# --- CONFIGURATION ---
CHALLENGE_START_DATE = datetime(2025, 12, 18) 

# Map ID to Name
SEGMENTS = {
    22655740: "Five Finger Hills",
    40409507: "Lakeshore Coxwell-Leslie",
    8223506:  "Pool to boardwalk",
    3219147:  "Scarborough Road",
    40410183: "Rainsford Rd",
    1705023:  "Stairway to Heavan",
    24820256: "Waterworks"
}

SEGMENT_IDS = list(SEGMENTS.keys())

# --- GOOGLE SHEETS SETUP ---
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    return gspread.authorize(creds)

def get_spreadsheet():
    client = get_client()
    return client.open(st.secrets["google"]["sheet_name"])

# Initialize connection
sh = get_spreadsheet()   # This is the FILE
sheet = sh.sheet1        # This is the specific TAB (Leaderboard)

def init_db(spreadsheet_obj):
    """Updates headers and ensures ActivityFeed tab exists."""
    # 1. Main Sheet Headers
    headers = ["athlete_id", "name", "refresh_token", "last_synced", "total_count"] + list(SEGMENTS.values())
    
    # We use spreadsheet_obj.sheet1 here, which is correct because we passed 'sh'
    main_ws = spreadsheet_obj.sheet1
    
    try:
        current_headers = main_ws.row_values(1)
    except:
        current_headers = []

    if not current_headers or current_headers != headers:
        main_ws.update(range_name='A1', values=[headers])

    # 2. Activity Feed Sheet (The Carousel Data)
    try:
        spreadsheet_obj.worksheet("ActivityFeed")
    except:
        # Create it if it doesn't exist
        ws = spreadsheet_obj.add_worksheet(title="ActivityFeed", rows=1000, cols=6)
        # Headers: Runner, Date, Title, Description, Distance, Kudos
        ws.append_row(["Runner", "Timestamp", "Title", "Description", "Distance", "Kudos"])

def update_last_edit():
    """Saves the current Eastern Time to the Metadata tab."""
    try:
        ws = sh.worksheet("Metadata")
    except:
        ws = sh.add_worksheet(title="Metadata", rows=5, cols=2)
    
    now_et = pd.Timestamp.now('America/Toronto').strftime('%Y-%m-%d %I:%M %p ET')
    ws.update_acell('A1', now_et)

def get_last_edit_time():
    try:
        ws = sh.worksheet("Metadata")
        return ws.acell('A1').value
    except:
        return "No updates yet"

# --- STRAVA API FUNCTIONS ---
def get_new_token(refresh_token):
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': st.secrets["strava"]["client_id"],
        'client_secret': st.secrets["strava"]["client_secret"],
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    })
    if res.status_code == 200:
        return res.json().get('access_token')
    return None

def fetch_efforts(access_token, start_epoch, runner_name):
    """Fetches segments AND activity metadata for the feed."""
    headers = {'Authorization': f"Bearer {access_token}"}
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    
    counts = {seg_id: 0 for seg_id in SEGMENT_IDS}
    feed_items = [] # Store new runs for the carousel
    latest_run_epoch = start_epoch

    params = {'after': start_epoch, 'per_page': 50}
    response = requests.get(activities_url, headers=headers, params=params)
    
    if response.status_code != 200:
        return counts, latest_run_epoch, feed_items

    activities = response.json()
    if not activities:
        return counts, latest_run_epoch, feed_items
    
    for act in activities:
        run_ts = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        if run_ts > latest_run_epoch:
            latest_run_epoch = int(run_ts)
            
        time.sleep(0.5) 
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers)
        
        if detail_res.status_code == 200:
            data = detail_res.json()
            
            # 1. Count Segments
            efforts = data.get('segment_efforts', [])
            for effort in efforts:
                sid = effort['segment']['id']
                if sid in counts:
                    counts[sid] += 1
            
            # 2. Extract Feed Data (Title, Description, Kudos)
            # Only add to feed if it's a Run/Walk (exclude random swims if any)
            if data.get('type') in ['Run', 'Walk', 'Hike']:
                feed_items.append([
                    runner_name,
                    act['start_date'], # ISO String
                    data.get('name', 'Run'),
                    data.get('description', ''), # The Caption
                    round(data.get('distance', 0) / 1000, 2), # KM
                    data.get('kudos_count', 0)
                ])
                    
    return counts, latest_run_epoch, feed_items

# --- UI LAYOUT ---
st.set_page_config(page_title="Run The Beaches Toronto!", page_icon="🏃", layout="centered")

# Ensure headers/sheets are correct
# FIX: Pass 'sh' (The Spreadsheet), not 'sheet' (The Worksheet)
init_db(sh)

# --- HEADER & ACTIVITY FEED ---
st.markdown("<h1 style='text-align: center;'>🏃 Run The Beaches Toronto Segment Challenge</h1>", unsafe_allow_html=True)

# 1. FETCH & CLEAN MAIN DATA
data = sheet.get_all_records()
df = pd.DataFrame(data)

if not df.empty:
    df.columns = df.columns.astype(str).str.strip()
    
    # Force integers for segments
    for seg_name in SEGMENTS.values():
        if seg_name in df.columns:
            df[seg_name] = pd.to_numeric(df[seg_name], errors='coerce').fillna(0).astype(int)

    # 2. ACTIVITY CAROUSEL (Fresh Off The Press)
    try:
        feed_ws = sh.worksheet("ActivityFeed")
        feed_data = feed_ws.get_all_records()
        df_feed = pd.DataFrame(feed_data)
        
        if not df_feed.empty:
            # Sort by Date (Newest First)
            df_feed = df_feed.sort_values(by="Timestamp", ascending=False).head(4)
            
            st.caption("🔥 Fresh off the press")
            cols = st.columns(4)
            for i, (_, row) in enumerate(df_feed.iterrows()):
                with cols[i % 4]:
                    with st.container(border=True):
                        st.markdown(f"**{row['Runner']}**")
                        st.caption(f"{row['Title']}")
                        if row['Description']:
                            st.info(f"_{row['Description']}_")
                        else:
                            st.write("") # Spacer
                        
                        st.markdown(f"👍 {row['Kudos']} | 📏 {row['Distance']}km")
    except:
        pass # Fail silently if feed tab is broken or empty

    st.divider()

    # 3. OVERALL LEADER
    segment_leaders = []
    for seg_name in SEGMENTS.values():
        if seg_name in df.columns:
            max_val = df[seg_name].max()
            if max_val > 0:
                leaders = df[df[seg_name] == max_val]['name'].tolist()
                segment_leaders.extend(leaders)
    
    if segment_leaders:
        win_counts = pd.Series(segment_leaders).value_counts()
        max_wins = win_counts.max()
        champions = win_counts[win_counts == max_wins].index.tolist()
        clean_champs = [c.replace(" *", "") for c in champions]
        st.info(f"👑 **Current Leader:** {', '.join(clean_champs)} ({max_wins} Segments Won)")
    else:
        st.info("👑 Current Leader: None yet!")

    # 4. SEGMENT LEADERBOARDS
    if list(SEGMENTS.values())[0] in df.columns:
        tabs = st.tabs(list(SEGMENTS.values()))
        
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i]:
                # Prepare Display DataFrame with Colors
                df_viz = df.copy()
                
                def format_status_name(row):
                    clean_name = row['name'].replace(" *", "")
                    if row['refresh_token'] == "SCRAPED" or row['refresh_token'] == "MANUAL":
                        return f"🔴 {clean_name}"
                    else:
                        return f"🟢 {clean_name}"
                
                df_viz['display_name'] = df_viz.apply(format_status_name, axis=1)

                seg_df = df_viz[['display_name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
                seg_df = seg_df[seg_df[seg_name] > 0] 
                
                if not seg_df.empty:
                    display_df = seg_df.copy()
                    
                    if len(display_df) >= 1: display_df.iloc[0, 0] = "🥇 " + display_df.iloc[0, 0] 
                    if len(display_df) >= 2: display_df.iloc[1, 0] = "🥈 " + display_df.iloc[1, 0]
                    if len(display_df) >= 3: display_df.iloc[2, 0] = "🥉 " + display_df.iloc[2, 0]

                    st.dataframe(
                        display_df,
                        column_config={
                            "display_name": "Runner",
                            seg_name: st.column_config.NumberColumn("Efforts", format="%d ⚡")
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.caption("No efforts recorded yet.")

    # 5. VISUALIZATION / STRATEGY
    st.divider()
    st.header("📊 Race Analysis")
    
    with st.expander("🎯 Strategy: The Bounty Board", expanded=False):
        runner_list = df['name'].tolist()
        me = st.selectbox("Select yourself to see gaps:", runner_list, index=0)
        
        my_row = df[df['name'] == me].iloc[0]
        strategy_data = []
        
        for seg_name in SEGMENTS.values():
            if seg_name in df.columns:
                current_leader_val = df[seg_name].max()
                my_val = int(my_row[seg_name])
                gap = current_leader_val - my_val
                
                strategy_data.append({
                    "Segment": seg_name,
                    "My Efforts": my_val,
                    "Legend's Score": current_leader_val,
                    "Gap to 1st": gap
                })
        
        if strategy_data:
            strat_df = pd.DataFrame(strategy_data)
            strat_df = strat_df.sort_values(by="Gap to 1st", ascending=True)