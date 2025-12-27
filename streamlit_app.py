import streamlit as st
import pandas as pd
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
import random
import altair as alt

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
sh = get_spreadsheet()
sheet = sh.sheet1

def init_db(sheet):
    """Updates headers if they don't match the current config."""
    headers = ["athlete_id", "name", "refresh_token", "last_synced", "total_count"] + list(SEGMENTS.values())
    
    try:
        current_headers = sheet.row_values(1)
    except:
        current_headers = []

    if not current_headers or current_headers != headers:
        sheet.update(range_name='A1', values=[headers])

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
            
        time.sleep(0.5) 
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
st.set_page_config(page_title="Run The Beaches Toronto!", page_icon="🏃", layout="centered")

# Ensure headers are correct before loading data
init_db(sheet)

# --- HEADER & WINNER ---
st.markdown("<h1 style='text-align: center;'>🏃 Run The Beaches Toronto Segment Challenge</h1>", unsafe_allow_html=True)

data = sheet.get_all_records()
df = pd.DataFrame(data)

if not df.empty:
    # 1. CLEAN DATA IMMEDIATELY
    df.columns = df.columns.astype(str).str.strip()
    
    # Force all segment columns to be integers
    for seg_name in SEGMENTS.values():
        if seg_name in df.columns:
            df[seg_name] = pd.to_numeric(df[seg_name], errors='coerce').fillna(0).astype(int)

    st.divider()

    # 2. OVERALL LEADER
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
        st.info(f"👑 **Current Legend:** {', '.join(champions)} ({max_wins} Segments Won)")
    else:
        st.info("👑 Current Legend: None yet!")

    # 3. SEGMENT LEADERBOARDS (Moved to Top)
    if list(SEGMENTS.values())[0] in df.columns:
        tabs = st.tabs(list(SEGMENTS.values()))
        
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i]:
                # Filter > 0 and sort
                seg_df = df[['name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
                seg_df = seg_df[seg_df[seg_name] > 0] 
                
                if not seg_df.empty:
                    display_df = seg_df.copy()
                    
                    # Add Medals
                    if len(display_df) >= 1: display_df.iloc[0, 0] = "🥇 " + display_df.iloc[0, 0] 
                    if len(display_df) >= 2: display_df.iloc[1, 0] = "🥈 " + display_df.iloc[1, 0]
                    if len(display_df) >= 3: display_df.iloc[2, 0] = "🥉 " + display_df.iloc[2, 0]

                    st.dataframe(
                        display_df,
                        column_config={
                            "name": "Runner",
                            seg_name: st.column_config.NumberColumn("Efforts", format="%d ⚡")
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.caption("No efforts recorded yet.")

    # 4. VISUALIZATION SECTION
    st.divider()
    st.header("📊 Race Analysis")
    
    # Strategy: "The Bounty Board" (Table View)
    with st.expander("🎯 Strategy: The Bounty Board", expanded=False):
        runner_list = df['name'].tolist()
        me = st.selectbox("Select yourself to see gaps:", runner_list, index=0)
        
        my_row = df[df['name'] == me].iloc[0]
        strategy_data = []
        
        for seg_name in SEGMENTS.values():
            if seg_name in df.columns:
                current_leader_val = df[seg_name].max()
                my_val = int(my_row[seg_name])
                
                # Calculate Gap
                gap = current_leader_val - my_val
                
                strategy_data.append({
                    "Segment": seg_name,
                    "My Efforts": my_val,
                    "Legend's Score": current_leader_val,
                    "Gap to 1st": gap
                })
        
        if strategy_data:
            # Create DataFrame and Sort by Gap (Smallest gap first)
            strat_df = pd.DataFrame(strategy_data)
            strat_df = strat_df.sort_values(by="Gap to 1st", ascending=True)
            
            # --- ADDED TEXT SUMMARY HERE ---
            # Filter for "Close" targets (e.g., 5 efforts or less)
            close_targets = strat_df[strat_df['Gap to 1st'] <= 5]
            
            if not close_targets.empty:
                # Get the names of the top 3 easiest segments
                target_names = close_targets['Segment'].tolist()
                target_str = ", ".join(target_names[:3])
                if len(target_names) > 3:
                    target_str += f", and {len(target_names)-3} others"
                
                st.write(f"🔥 **{me}**, you are within striking distance (5 efforts or less) on: **{target_str}**!")
            else:
                st.write(f"💪 **{me}**, look at the top of the list below for your best target.")
            # -------------------------------

            st.dataframe(
                strat_df,
                column_config={
                    "Gap to 1st": st.column_config.ProgressColumn(
                        "Gap to Legend",
                        help="How many more runs you need to tie",
                        format="%d",
                        min_value=0,
                        max_value=int(strat_df["Gap to 1st"].max())
                    ),
                },
                hide_index=True,
                use_container_width=True
            )

    
else:
    st.info("Starting up... No data found yet.")

# --- SIDEBAR: JOIN & ADMIN ---
with st.sidebar:
    st.image("logo.png", use_container_width=True)
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
                update_last_edit() 
                st.balloons()
                st.success("Registered!")
                st.query_params.clear()

    # --- ADMIN SECTION ---
    st.divider()
    with st.expander("👮 Admin Access"):
        admin_pass = st.text_input("Password", type="password")
        
        if admin_pass == st.secrets["admin"]["password"]:
            st.success("Authenticated")
            
            # --- ADMIN TABS ---
            tab1, tab2, tab3, tab4 = st.tabs(["Add", "Edit", "Sync", "Delete"])

            # 1. ADD NEW RUNNER
            with tab1:
                st.caption("Add Manual Runner (marked with *)")
                with st.form("add_runner_form"):
                    new_name = st.text_input("Name")
                    
                    new_values = {}
                    cols = st.columns(2)
                    for i, seg_name in enumerate(SEGMENTS.values()):
                        with cols[i % 2]:
                            new_values[seg_name] = st.number_input(seg_name, min_value=0, value=0, key=f"add_{seg_name}")
                    
                    if st.form_submit_button("Add Runner"):
                        if new_name:
                            final_name = f"{new_name} *"
                            fake_id = random.randint(10000000, 99999999)
                            total = sum(new_values.values())
                            segment_vals = [new_values[s] for s in SEGMENTS.values()]
                            
                            new_row = [fake_id, final_name, "MANUAL", 0, total] + segment_vals
                            
                            sheet.append_row(new_row)
                            update_last_edit()
                            st.success(f"Added {final_name}!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Name is required.")

            # 2. EDIT EXISTING
            with tab2:
                records = sheet.get_all_records()
                df_edit = pd.DataFrame(records)
                
                if not df_edit.empty:
                    runner_names = df_edit['name'].tolist()
                    selected_runner = st.selectbox("Select Runner", runner_names, key="edit_select")
                    runner_row = df_edit[df_edit['name'] == selected_runner].iloc[0]
                    
                    with st.form("edit_form"):
                        edit_vals = {}
                        cols = st.columns(2)
                        for i, seg_name in enumerate(SEGMENTS.values()):
                            current_val = int(runner_row[seg_name])
                            with cols[i % 2]:
                                edit_vals[seg_name] = st.number_input(
                                    seg_name, 
                                    value=current_val, 
                                    min_value=0, 
                                    key=f"edit_{seg_name}_{selected_runner}"
                                )
                            
                        if st.form_submit_button("Save Changes"):
                            row_idx = df_edit[df_edit['name'] == selected_runner].index[0] + 2
                            
                            for seg_name, val in edit_vals.items():
                                col_idx = df_edit.columns.get_loc(seg_name) + 1
                                sheet.update_cell(row_idx, col_idx, val)
                                
                            new_total = sum(edit_vals.values())
                            total_col = df_edit.columns.get_loc("total_count") + 1
                            sheet.update_cell(row_idx, total_col, new_total)
                            
                            update_last_edit()
                            st.success("Updated!")
                            time.sleep(1)
                            st.rerun()

            # 3. SYNC ALL
            with tab3:
                st.caption("Syncs only connected Strava users.")
                if st.button("Start Sync"):
                    records = sheet.get_all_records()
                    bar = st.progress(0, text="Syncing...")
                    
                    for i, row in enumerate(records):
                        # SKIP MANUAL USERS
                        if row['refresh_token'] == "MANUAL":
                            continue

                        bar.progress((i) / len(records), text=f"Syncing {row['name']}...")
                        new_token = get_new_token(row['refresh_token'])
                        
                        if new_token:
                            last_epoch = row['last_synced']
                            new_counts, new_epoch = fetch_efforts(new_token, last_epoch)
                            total_new = sum(new_counts.values())
                            
                            if total_new > 0:
                                row_idx = i + 2
                                sheet.update_cell(row_idx, 4, new_epoch)
                                sheet.update_cell(row_idx, 5, row['total_count'] + total_new)
                                
                                for s_idx, sid in enumerate(SEGMENT_IDS):
                                    if new_counts[sid] > 0:
                                        col_idx = 6 + s_idx
                                        current_val = row[SEGMENTS[sid]]
                                        sheet.update_cell(row_idx, col_idx, current_val + new_counts[sid])
                        time.sleep(1)
                    
                    update_last_edit() 
                    bar.empty()
                    st.success("Sync Complete!")
                    time.sleep(1)
                    st.rerun()
            
            # 4. DELETE RUNNER
            with tab4:
                st.caption("⚠️ Permanently remove a runner")
                records = sheet.get_all_records()
                df_del = pd.DataFrame(records)
                
                if not df_del.empty:
                    runner_to_del = st.selectbox("Select Runner to Delete", df_del['name'].tolist(), key="del_select")
                    
                    if st.button("Delete Runner", type="primary"):
                        row_idx = df_del[df_del['name'] == runner_to_del].index[0] + 2
                        sheet.delete_rows(row_idx)
                        update_last_edit() 
                        st.success(f"Deleted {runner_to_del}")
                        time.sleep(1)
                        st.rerun()

st.caption(f"Last system update: {get_last_edit_time()}")