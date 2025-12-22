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

SEGMENT_IDS = list(SEGMENTS.keys())

# --- GOOGLE SHEETS SETUP ---
def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    client = gspread.authorize(creds)
    sheet = client.open(st.secrets["google"]["sheet_name"]).sheet1
    return sheet

def init_db(sheet):
    """Creates headers if the sheet is empty."""
    if not sheet.row_values(1):
        # 1-based index mapping:
        # 1:athlete_id, 2:name, 3:refresh_token, 4:last_synced_epoch, 5:total_count, 6+:Segments
        headers = ["athlete_id", "name", "refresh_token", "last_synced", "total_count"] + list(SEGMENTS.values())
        sheet.append_row(headers)

# --- STRAVA API FUNCTIONS ---
def get_new_token(refresh_token):
    """Exchanges a refresh token for a new access token."""
    res = requests.post("https://www.strava.com/oauth/token", data={
        'client_id': st.secrets["strava"]["client_id"],
        'client_secret': st.secrets["strava"]["client_secret"],
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    })
    if res.status_code == 200:
        return res.json().get('access_token')
    return None

def fetch_efforts(access_token, start_epoch):
    """Scans for segment efforts after a specific date."""
    headers = {'Authorization': f"Bearer {access_token}"}
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    
    # Init counters for each segment (set to 0)
    counts = {seg_id: 0 for seg_id in SEGMENT_IDS}
    latest_run_epoch = start_epoch

    # Get Summary Activities
    params = {'after': start_epoch, 'per_page': 50} # 50 is safe for a "refresh"
    response = requests.get(activities_url, headers=headers, params=params)
    
    if response.status_code != 200:
        return counts, latest_run_epoch

    activities = response.json()
    if not activities:
        return counts, latest_run_epoch
    
    for act in activities:
        # Update timestamp marker
        run_time = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        if run_time > latest_run_epoch:
            latest_run_epoch = int(run_time)
            
        # Get Detailed Activity
        time.sleep(0.5) # Rate limit protection
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers)
        
        if detail_res.status_code == 200:
            efforts = detail_res.json().get('segment_efforts', [])
            for effort in efforts:
                sid = effort['segment']['id']
                if sid in counts:
                    counts[sid] += 1
                    
    return counts, latest_run_epoch

# --- UI LAYOUT ---
st.set_page_config(page_title="Run Club Leaderboard", page_icon="🏃")
st.title("🏃 Segment Challenge Leaderboard")

sheet = get_sheet()
init_db(sheet)

# --- SIDEBAR: JOIN & ADMIN ---
with st.sidebar:
    st.header("Join Challenge")
    auth_url = (
        f"https://www.strava.com/oauth/authorize?client_id={st.secrets['strava']['client_id']}"
        f"&response_type=code&redirect_uri={st.secrets['strava']['redirect_uri']}"
        "&approval_prompt=force&scope=activity:read_all"
    )
    st.link_button("Connect Strava", auth_url)
    
    # Handle Callback
    if "code" in st.query_params:
        code = st.query_params["code"]
        # Exchange for token
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': st.secrets["strava"]["client_id"],
            'client_secret': st.secrets["strava"]["client_secret"],
            'code': code,
            'grant_type': 'authorization_code'
        })
        data = res.json()
        
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
                start_epoch = int(CHALLENGE_START_DATE.timestamp())
                counts, last_epoch = fetch_efforts(data['access_token'], start_epoch)
                
                # Prepare Row
                total = sum(counts.values())
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
                st.success(f"Registered! Found {total} efforts.")
                st.query_params.clear()

    # --- ADMIN SECTION ---
    st.divider()
    with st.expander("👮 Admin Access"):
        admin_pass = st.text_input("Password", type="password")
        
        if admin_pass == st.secrets["admin"]["password"]:
            st.success("Admin Mode Active")
            
            # --- OPTION A: REFRESH ALL ---
            if st.button("🔄 Sync All Athletes"):
                records = sheet.get_all_records()
                progress_bar = st.progress(0, text="Starting Sync...")
                
                for i, row in enumerate(records):
                    # Progress Update
                    progress_bar.progress((i) / len(records), text=f"Syncing {row['name']}...")
                    
                    # 1. Get New Access Token
                    new_token = get_new_token(row['refresh_token'])
                    
                    if new_token:
                        # 2. Find new efforts since last sync
                        last_epoch = row['last_synced']
                        new_counts, new_epoch = fetch_efforts(new_token, last_epoch)
                        
                        total_new = sum(new_counts.values())
                        
                        if total_new > 0:
                            # 3. Update Sheet
                            # Row index = i + 2 (header is 1, list is 0-indexed)
                            row_idx = i + 2
                            
                            # Update timestamp (Col 4)
                            sheet.update_cell(row_idx, 4, new_epoch)
                            
                            # Update Total (Col 5)
                            current_total = row['total_count']
                            sheet.update_cell(row_idx, 5, current_total + total_new)
                            
                            # Update Specific Segments (Cols 6+)
                            # We loop through SEGMENT_IDS to match the column order
                            for s_idx, sid in enumerate(SEGMENT_IDS):
                                if new_counts[sid] > 0:
                                    col_idx = 6 + s_idx
                                    current_val = row[SEGMENTS[sid]]
                                    sheet.update_cell(row_idx, col_idx, current_val + new_counts[sid])
                                    
                    time.sleep(1) # Safety buffer for API
                
                progress_bar.empty()
                st.toast("Sync Complete!")
                time.sleep(1)
                st.rerun()

            # --- OPTION B: MANUAL EDIT ---
            st.markdown("---")
            st.caption("Manual Override")
            
            records = sheet.get_all_records()
            df = pd.DataFrame(records)
            
            if not df.empty:
                runner = st.selectbox("Runner", df['name'].unique())
                segment = st.selectbox("Segment", list(SEGMENTS.values()))
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("➕ Add 1"):
                        row_idx = df[df['name'] == runner].index[0] + 2
                        
                        # Update Segment
                        seg_col = df.columns.get_loc(segment) + 1
                        val = df.loc[df['name'] == runner, segment].values[0]
                        sheet.update_cell(row_idx, seg_col, int(val) + 1)
                        
                        # Update Total
                        tot_col = df.columns.get_loc("total_count") + 1
                        tot = df.loc[df['name'] == runner, "total_count"].values[0]
                        sheet.update_cell(row_idx, tot_col, int(tot) + 1)
                        
                        st.toast(f"Added to {runner}")
                        time.sleep(1)
                        st.rerun()
                with col2:
                    if st.button("➖ Remove 1"):
                        row_idx = df[df['name'] == runner].index[0] + 2
                        
                        # Update Segment
                        seg_col = df.columns.get_loc(segment) + 1
                        val = df.loc[df['name'] == runner, segment].values[0]
                        sheet.update_cell(row_idx, seg_col, max(0, int(val) - 1))
                        
                        # Update Total
                        tot_col = df.columns.get_loc("total_count") + 1
                        tot = df.loc[df['name'] == runner, "total_count"].values[0]
                        sheet.update_cell(row_idx, tot_col, max(0, int(tot) - 1))
                        
                        st.toast(f"Removed from {runner}")
                        time.sleep(1)
                        st.rerun()

# --- DISPLAY LEADERBOARDS ---
data = sheet.get_all_records()

if data:
    df = pd.DataFrame(data)
    
    # Check if we have data to show
    if 'total_count' in df.columns:
        # Create Tabs
        tab_names = ["🏆 Overall"] + list(SEGMENTS.values())
        tabs = st.tabs(tab_names)
        
        # 1. Overall
        with tabs[0]:
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
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i + 1]:
                if seg_name in df.columns:
                    # Sort and filter for this segment
                    seg_df = df[['name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
                    # Optional: Filter out 0s if you want a cleaner view
                    # seg_df = seg_df[seg_df[seg_name] > 0] 
                    
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
        st.info("Database empty. Waiting for runners.")
else:
    st.info("No runners yet. Be the first to join!")

st.divider()
st.caption(f"Last system update: {datetime.now().strftime('%H:%M UTC')}")