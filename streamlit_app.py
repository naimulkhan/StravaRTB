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
        # Headers: Meta data first, then segments
        headers = ["athlete_id", "name", "refresh_token", "last_synced", "total_count"] + list(SEGMENTS.values())
        sheet.append_row(headers)

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

def fetch_efforts(access_token, start_epoch):
    headers = {'Authorization': f"Bearer {access_token}"}
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    
    counts = {seg_id: 0 for seg_id in SEGMENT_IDS}
    latest_run_epoch = start_epoch

    params = {'after': start_epoch, 'per_page': 50}
    response = requests.get(activities_url, headers=headers, params=params)
    
    if response.status_code != 200:
        return counts, latest_run_epoch

    activities = response.json()
    if not activities:
        return counts, latest_run_epoch
    
    for act in activities:
        run_time = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        if run_time > latest_run_epoch:
            latest_run_epoch = int(run_time)
            
        time.sleep(0.5) # Safety buffer
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

sheet = get_sheet()
init_db(sheet)

# --- HEADER & WINNER CALCULATION ---
st.title("🏃 Segment Challenge")

# Fetch Data for display
data = sheet.get_all_records()

if data:
    df = pd.DataFrame(data)
    
    # CALCULATE "GRAND WINNER" (Most Segments Won)
    # 1. Identify leader for each segment
    segment_leaders = []
    if not df.empty and list(SEGMENTS.values())[0] in df.columns:
        for seg_name in SEGMENTS.values():
            # Find max value for this segment
            max_val = df[seg_name].max()
            if max_val > 0:
                # Find all runners who have this max value (handling ties)
                leaders = df[df[seg_name] == max_val]['name'].tolist()
                segment_leaders.extend(leaders)
        
        # 2. Count "Wins" per runner
        if segment_leaders:
            win_counts = pd.Series(segment_leaders).value_counts()
            max_wins = win_counts.max()
            # Get runners with the most wins
            champions = win_counts[win_counts == max_wins].index.tolist()
            
            # 3. Display
            st.info(f"👑 **Current Leader:** {', '.join(champions)} ({max_wins} Segments Won)")
        else:
            st.info("👑 Current Leader: None yet!")

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
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': st.secrets["strava"]["client_id"],
            'client_secret': st.secrets["strava"]["client_secret"],
            'code': code,
            'grant_type': 'authorization_code'
        })
        data_json = res.json()
        
        if "access_token" in data_json:
            ath = data_json['athlete']
            records = sheet.get_all_records()
            df_auth = pd.DataFrame(records)
            
            is_registered = False
            if not df_auth.empty and 'athlete_id' in df_auth.columns:
                 if ath['id'] in df_auth['athlete_id'].values:
                     is_registered = True
            
            if is_registered:
                st.warning("You are already registered!")
            else:
                st.info("Scanning history... please wait.")
                start_epoch = int(CHALLENGE_START_DATE.timestamp())
                counts, last_epoch = fetch_efforts(data_json['access_token'], start_epoch)
                
                total = sum(counts.values())
                segment_values = [counts[sid] for sid in SEGMENT_IDS]
                
                new_row = [
                    ath['id'], 
                    f"{ath['firstname']} {ath['lastname']}", 
                    data_json['refresh_token'], 
                    last_epoch,
                    total
                ] + segment_values
                
                sheet.append_row(new_row)
                st.balloons()
                st.success("Registered!")
                st.query_params.clear()

    # --- ADMIN SECTION ---
    st.divider()
    with st.expander("👮 Admin Access"):
        admin_pass = st.text_input("Password", type="password")
        
        if admin_pass == st.secrets["admin"]["password"]:
            st.success("Authenticated")
            
            # OPTION A: SYNC ALL
            if st.button("🔄 Sync All Athletes"):
                records = sheet.get_all_records()
                bar = st.progress(0, text="Syncing...")
                
                for i, row in enumerate(records):
                    bar.progress((i) / len(records), text=f"Syncing {row['name']}...")
                    new_token = get_new_token(row['refresh_token'])
                    
                    if new_token:
                        last_epoch = row['last_synced']
                        new_counts, new_epoch = fetch_efforts(new_token, last_epoch)
                        total_new = sum(new_counts.values())
                        
                        if total_new > 0:
                            row_idx = i + 2
                            # Update Time & Total
                            sheet.update_cell(row_idx, 4, new_epoch)
                            sheet.update_cell(row_idx, 5, row['total_count'] + total_new)
                            
                            # Update Segments
                            for s_idx, sid in enumerate(SEGMENT_IDS):
                                if new_counts[sid] > 0:
                                    col_idx = 6 + s_idx
                                    current_val = row[SEGMENTS[sid]]
                                    sheet.update_cell(row_idx, col_idx, current_val + new_counts[sid])
                    time.sleep(1)
                bar.empty()
                st.toast("Sync Complete!")
                time.sleep(1)
                st.rerun()

            # OPTION B: BULK EDIT RUNNER
            st.markdown("---")
            st.caption("Edit Runner Stats")
            
            records = sheet.get_all_records()
            df_edit = pd.DataFrame(records)
            
            if not df_edit.empty:
                # 1. Select Runner
                runner_names = df_edit['name'].tolist()
                selected_runner = st.selectbox("Select Runner to Edit", runner_names)
                
                # 2. Show Form with ALL segments
                runner_row = df_edit[df_edit['name'] == selected_runner].iloc[0]
                
                with st.form("edit_form"):
                    st.write(f"Editing: **{selected_runner}**")
                    new_values = {}
                    
                    # Create a number input for every segment
                    # We start looping from SEGMENT definitions to keep order
                    for seg_name in SEGMENTS.values():
                        current_val = int(runner_row[seg_name])
                        new_values[seg_name] = st.number_input(seg_name, value=current_val, min_value=0)
                        
                    if st.form_submit_button("Save Changes"):
                        # Find Row Index
                        row_idx = df_edit[df_edit['name'] == selected_runner].index[0] + 2
                        
                        # Update every column
                        for seg_name, val in new_values.items():
                            col_idx = df_edit.columns.get_loc(seg_name) + 1
                            sheet.update_cell(row_idx, col_idx, val)
                            
                        # Recalculate Total
                        new_total = sum(new_values.values())
                        total_col = df_edit.columns.get_loc("total_count") + 1
                        sheet.update_cell(row_idx, total_col, new_total)
                        
                        st.success("Updated!")
                        time.sleep(1)
                        st.rerun()

# --- DISPLAY LEADERBOARDS ---
# data was fetched at the top
if data:
    df_disp = pd.DataFrame(data)
    
    if list(SEGMENTS.values())[0] in df_disp.columns:
        # Create Tabs for Segments ONLY (No Overall)
        tabs = st.tabs(list(SEGMENTS.values()))
        
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i]:
                # Filter: Show only runners with > 0 efforts
                seg_df = df_disp[['name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
                seg_df = seg_df[seg_df[seg_name] > 0] 
                
                if not seg_df.empty:
                    # Highlight Top 1
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
                    st.caption("No efforts recorded for this segment yet.")
    else:
        st.info("Database empty. Waiting for runners.")
else:
    st.info("No runners yet. Be the first to join!")

st.divider()
st.caption(f"Last system update: {datetime.now().strftime('%H:%M UTC')}")