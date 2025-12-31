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
sh = get_spreadsheet()
sheet = sh.sheet1

def init_db(spreadsheet_obj):
    """Updates headers and ensures ActivityFeed tab exists."""
    headers = ["athlete_id", "name", "refresh_token", "last_synced", "total_count"] + list(SEGMENTS.values())
    main_ws = spreadsheet_obj.sheet1
    try:
        current_headers = main_ws.row_values(1)
    except:
        current_headers = []

    if not current_headers or current_headers != headers:
        main_ws.update(range_name='A1', values=[headers])

    try:
        spreadsheet_obj.worksheet("ActivityFeed")
    except:
        ws = spreadsheet_obj.add_worksheet(title="ActivityFeed", rows=1000, cols=6)
        ws.append_row(["Runner", "Timestamp", "Title", "Description", "Distance", "Kudos"])

def update_last_edit():
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

def fetch_activities(access_token, start_epoch, runner_name):
    """
    Fetches activities. 
    CRITICAL: Only adds to Feed if a Challenge Segment was actually run.
    """
    headers = {'Authorization': f"Bearer {access_token}"}
    activities_url = "https://www.strava.com/api/v3/athlete/activities"
    
    # Initialize counts for this batch
    counts = {seg_id: 0 for seg_id in SEGMENT_IDS}
    feed_items = [] 
    
    current_max_epoch = start_epoch 

    params = {'after': start_epoch, 'per_page': 50}
    response = requests.get(activities_url, headers=headers, params=params)
    
    if response.status_code != 200:
        return counts, current_max_epoch, feed_items

    activities = response.json()
    if not activities:
        return counts, current_max_epoch, feed_items
    
    for act in activities:
        # 1. Skip non-run activities immediately
        if act.get('type') not in ['Run', 'Walk', 'Hike']:
            continue

        run_ts = datetime.strptime(act['start_date'], "%Y-%m-%dT%H:%M:%SZ").timestamp()
        if run_ts > current_max_epoch:
            current_max_epoch = int(run_ts)
            
        time.sleep(0.5) 
        detail_url = f"https://www.strava.com/api/v3/activities/{act['id']}"
        detail_res = requests.get(detail_url, headers=headers)
        
        if detail_res.status_code == 200:
            data = detail_res.json()
            
            # 2. Count segments for THIS specific run
            efforts = data.get('segment_efforts', [])
            segments_matched_in_this_run = 0
            
            for effort in efforts:
                sid = effort['segment']['id']
                if sid in counts:
                    counts[sid] += 1
                    segments_matched_in_this_run += 1
            
            # 3. ONLY add to Feed if this run matched at least 1 Challenge Segment
            if segments_matched_in_this_run > 0:
                feed_items.append([
                    runner_name,
                    act['start_date_local'], # Use Local Time
                    data.get('name', 'Run'),
                    data.get('description', ''),
                    round(data.get('distance', 0) / 1000, 2),
                    data.get('kudos_count', 0)
                ])
                    
    return counts, current_max_epoch, feed_items

# --- UI LAYOUT ---
st.set_page_config(page_title="Run The Beaches Toronto!", page_icon="🏃", layout="wide") 

init_db(sh)

st.markdown("<h1 style='text-align: center;'>🏃 Run The Beaches Toronto Segment Challenge</h1>", unsafe_allow_html=True)

data = sheet.get_all_records()
df = pd.DataFrame(data)

if not df.empty:
    df.columns = df.columns.astype(str).str.strip()
    
    for seg_name in SEGMENTS.values():
        if seg_name in df.columns:
            df[seg_name] = pd.to_numeric(df[seg_name], errors='coerce').fillna(0).astype(int)

    # 1. ACTIVITY CAROUSEL
    try:
        feed_ws = sh.worksheet("ActivityFeed")
        feed_data = feed_ws.get_all_records()
        df_feed = pd.DataFrame(feed_data)
        
        if not df_feed.empty:
            df_feed['Timestamp_Obj'] = pd.to_datetime(df_feed['Timestamp'])
            # Sort newest first, show top 10
            df_feed = df_feed.sort_values(by="Timestamp_Obj", ascending=False).head(10)
            
            st.caption("🔥 Fresh off the press")
            
            # CSS for Horizontal Scroll Snap
            st.markdown(f"""
            <style>
                .scroll-container {{
                    display: flex;
                    overflow-x: auto;
                    padding-bottom: 20px;
                    padding-top: 5px;
                    gap: 15px;
                    scrollbar-width: thin;
                }}
                .card {{
                    min-width: 280px;
                    max-width: 280px;
                    background-color: #262730; 
                    border: 1px solid #41444C;
                    border-radius: 10px;
                    padding: 15px;
                    display: flex;
                    flex-direction: column;
                    justify-content: space-between;
                    box-shadow: 2px 2px 5px rgba(0,0,0,0.3);
                }}
                .card-header {{
                    display: flex;
                    justify-content: space-between;
                    font-size: 0.9em;
                    color: #FAFAFA;
                    margin-bottom: 5px;
                }}
                .card-date {{
                    color: #A3A8B8;
                    font-size: 0.8em;
                }}
                .card-title {{
                    font-weight: bold;
                    font-size: 1.1em;
                    color: #FF4B4B; 
                    margin-bottom: 10px;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }}
                .card-body {{
                    font-size: 0.85em;
                    color: #E6EAF1;
                    background-color: #31333F;
                    padding: 10px;
                    border-radius: 5px;
                    margin-bottom: 10px;
                    height: 60px;
                    overflow-y: auto;
                    font-style: italic;
                }}
                .card-footer {{
                    display: flex;
                    justify-content: space-between;
                    font-size: 0.9em;
                    color: #FAFAFA;
                    font-weight: bold;
                }}
            </style>
            """, unsafe_allow_html=True)

            cards_html = ""
            for i, row in df_feed.iterrows():
                desc = str(row['Description'])
                if desc == "nan" or desc == "": desc = "No comments."
                desc = desc.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
                
                date_str = row['Timestamp_Obj'].strftime('%b %d')
                
                cards_html += f"""
                <div class="card">
                    <div class="card-header">
                        <strong>{row['Runner']}</strong>
                        <span class="card-date">{date_str}</span>
                    </div>
                    <div class="card-title">{row['Title']}</div>
                    <div class="card-body">{desc}</div>
                    <div class="card-footer">
                        <span>👍 {row['Kudos']}</span>
                        <span>📏 {row['Distance']} km</span>
                    </div>
                </div>
                """
            
            st.markdown(f'<div class="scroll-container">{cards_html}</div>', unsafe_allow_html=True)

    except Exception as e:
        pass

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
        clean_champs = [c.replace(" *", "") for c in champions]
        st.info(f"👑 **Current Legend:** {', '.join(clean_champs)} ({max_wins} Segments Won)")
    else:
        st.info("👑 Current Legend: None yet!")

    # 3. SEGMENT LEADERBOARDS
    if list(SEGMENTS.values())[0] in df.columns:
        tabs = st.tabs(list(SEGMENTS.values()))
        
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i]:
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

    # 4. STRATEGY
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
            
            owned_segments = strat_df[strat_df['Gap to 1st'] == 0]['Segment'].tolist()
            close_targets = strat_df[(strat_df['Gap to 1st'] > 0) & (strat_df['Gap to 1st'] <= 5)]['Segment'].tolist()

            if owned_segments:
                seg_str = ", ".join(owned_segments[:3])
                if len(owned_segments) > 3: seg_str += f" and {len(owned_segments)-3} others"
                st.success(f"👑 **Bow down!** {me}, you are the **Local Legend** on: **{seg_str}**. Heavy is the head that wears the crown! 👑")

            if close_targets:
                target_str = ", ".join(close_targets[:3])
                if len(close_targets) > 3: target_str += f", and {len(close_targets)-3} others"
                st.warning(f"👀 **They can hear your footsteps!** You are within striking distance (5 or less) on: **{target_str}**. Drink some coffee and go steal that glory!")
            
            if not owned_segments and not close_targets:
                st.info(f"💪 **{me}**, you've got some work to do. Tie your laces tight and start chipping away at the list below!")

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
            new_full_name = f"{ath['firstname']} {ath['lastname']}"
            
            records = sheet.get_all_records()
            df_auth = pd.DataFrame(records)
            
            is_already_connected = False
            if not df_auth.empty and 'athlete_id' in df_auth.columns:
                 if ath['id'] in df_auth['athlete_id'].values:
                     is_already_connected = True
            
            if is_already_connected:
                st.warning("You are already connected!")
            else:
                if not df_auth.empty:
                    df_auth['clean_name'] = df_auth['name'].astype(str).str.replace(" *", "").str.strip()
                    scraped_match = df_auth[
                        (df_auth['clean_name'] == new_full_name) & 
                        (df_auth['refresh_token'] == 'SCRAPED')
                    ]
                    if not scraped_match.empty:
                        row_to_delete = scraped_match.index[0] + 2 
                        sheet.delete_rows(row_to_delete)
                        st.caption(f"Upgraded {new_full_name} from Scraped to Connected! 🟢")
                        time.sleep(1)

                st.info("Scanning history... please wait.")
                start_epoch = int(CHALLENGE_START_DATE.timestamp())
                counts, last_epoch, feed_items = fetch_activities(data_json['access_token'], start_epoch, new_full_name)
                
                if feed_items:
                    try:
                        fw = sh.worksheet("ActivityFeed")
                        fw.append_rows(feed_items)
                    except: pass

                total = sum(counts.values())
                segment_values = [counts[sid] for sid in SEGMENT_IDS]
                
                new_row = [
                    ath['id'], 
                    new_full_name,
                    data_json['refresh_token'], 
                    last_epoch,
                    total
                ] + segment_values
                
                sheet.append_row(new_row)
                update_last_edit() 
                st.balloons()
                st.success("Registered! You are now Connected 🟢")
                st.query_params.clear()

    # --- ADMIN SECTION ---
    st.divider()
    with st.expander("👮 Admin Access"):
        admin_pass = st.text_input("Password", type="password")
        
        if admin_pass == st.secrets["admin"]["password"]:
            st.success("Authenticated")
            
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
                                edit_vals[seg_name] = st.number_input(seg_name, value=current_val, min_value=0, key=f"edit_{seg_name}_{selected_runner}")
                            
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
                
                # --- NEW FORCE RESYNC CHECKBOX ---
                force_full_sync = st.checkbox("Force Full History Resync (Check this if you cleared the feed)")
                
                if st.button("Start Sync"):
                    # 1. READ ALL DATA INTO MEMORY ONCE
                    records = sheet.get_all_records()
                    df_sync = pd.DataFrame(records)
                    
                    numeric_cols = ["total_count", "last_synced"] + list(SEGMENTS.values())
                    for c in numeric_cols:
                        if c in df_sync.columns:
                            df_sync[c] = pd.to_numeric(df_sync[c], errors='coerce').fillna(0).astype(int)

                    try:
                        feed_ws = sh.worksheet("ActivityFeed")
                        existing_feed = feed_ws.get_all_records()
                        existing_keys = set((row['Runner'], row['Timestamp']) for row in existing_feed)
                    except:
                        existing_keys = set()

                    bar = st.progress(0, text="Syncing...")
                    all_new_feed_items = []
                    updates_made = False
                    
                    # 2. LOOP AND UPDATE IN MEMORY
                    for i, row in df_sync.iterrows():
                        if row['refresh_token'] == "MANUAL" or row['refresh_token'] == "SCRAPED":
                            continue

                        bar.progress((i) / len(df_sync), text=f"Syncing {row['name']}...")
                        new_token = get_new_token(row['refresh_token'])
                        
                        if new_token:
                            clean_name = row['name'].replace(" *", "")
                            
                            # --- HYBRID SYNC LOGIC ---
                            last_sync_ts = row.get('last_synced', 0)
                            start_ts = int(CHALLENGE_START_DATE.timestamp())
                            
                            # LOGIC: Force Full OR Standard Incremental Check
                            if force_full_sync:
                                fetch_mode = "FULL"
                                fetch_start = start_ts
                            elif last_sync_ts and int(last_sync_ts) > start_ts:
                                fetch_mode = "INCREMENTAL"
                                fetch_start = int(last_sync_ts)
                            else:
                                fetch_mode = "FULL"
                                fetch_start = start_ts

                            new_counts, new_epoch, new_feed = fetch_activities(new_token, fetch_start, clean_name)
                            
                            for item in new_feed:
                                key = (item[0], item[1])
                                if key not in existing_keys:
                                    all_new_feed_items.append(item)
                                    existing_keys.add(key)
                            
                            # UPDATE DATAFRAME (NOT SHEET)
                            df_sync.at[i, 'last_synced'] = new_epoch
                            
                            total_new = sum(new_counts.values())
                            
                            if fetch_mode == "INCREMENTAL":
                                df_sync.at[i, 'total_count'] += total_new
                                for sid in SEGMENT_IDS:
                                    sname = SEGMENTS[sid]
                                    df_sync.at[i, sname] += new_counts[sid]
                            else:
                                # FULL SYNC OVERWRITES
                                df_sync.at[i, 'total_count'] = total_new
                                for sid in SEGMENT_IDS:
                                    sname = SEGMENTS[sid]
                                    df_sync.at[i, sname] = new_counts[sid]
                            
                            updates_made = True
                                
                        time.sleep(1)
                    
                    # 3. BATCH WRITE EVERYTHING BACK ONCE
                    if updates_made:
                        # Convert DataFrame back to list of lists for GSpread
                        df_sync = df_sync.fillna(0)
                        data_to_upload = [df_sync.columns.values.tolist()] + df_sync.values.tolist()
                        
                        # One API call to rule them all
                        sheet.update(range_name='A1', values=data_to_upload)

                    if all_new_feed_items:
                        try:
                            fw = sh.worksheet("ActivityFeed")
                            fw.append_rows(all_new_feed_items)
                        except: pass

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