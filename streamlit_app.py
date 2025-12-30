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
    
    # Force integers
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
        # Clean names for display (remove *)
        clean_champs = [c.replace(" *", "") for c in champions]
        st.info(f"👑 **Current Leader:** {', '.join(clean_champs)} ({max_wins} Segments Won)")
    else:
        st.info("👑 Current Leader: None yet!")

    # 3. SEGMENT LEADERBOARDS
    if list(SEGMENTS.values())[0] in df.columns:
        tabs = st.tabs(list(SEGMENTS.values()))
        
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i]:
                # Prepare Display DataFrame with Colors
                # We do this BEFORE filtering/sorting so we have access to refresh_token
                df_viz = df.copy()
                
                # Apply Green/Red Indicator based on connection status
                def format_status_name(row):
                    clean_name = row['name'].replace(" *", "")
                    if row['refresh_token'] == "SCRAPED" or row['refresh_token'] == "MANUAL":
                        return f"🔴 {clean_name}"
                    else:
                        return f"🟢 {clean_name}"
                
                df_viz['display_name'] = df_viz.apply(format_status_name, axis=1)

                # Filter > 0 and sort
                seg_df = df_viz[['display_name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
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
                            "display_name": "Runner",
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
            
            # Text Logic
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

    col_viz1, col_viz2 = st.columns(2)

    with col_viz1:
        st.subheader("🔥 Effort Heatmap")
        seg_cols = list(SEGMENTS.values())
        valid_seg_cols = [c for c in seg_cols if c in df.columns]
        
        if valid_seg_cols:
            heat_data = df.melt(id_vars=['name'], value_vars=valid_seg_cols, var_name='Segment', value_name='Efforts')
            heat_data = heat_data[heat_data['Efforts'] > 0]

            c = alt.Chart(heat_data).mark_rect().encode(
                x=alt.X('Segment:N', axis=alt.Axis(labelAngle=-45)),
                y=alt.Y('name:N', title=None),
                color=alt.Color('Efforts:Q', scale=alt.Scale(scheme='orangered')),
                tooltip=['name', 'Segment', 'Efforts']
            ).properties(height=350)
            
            st.altair_chart(c, use_container_width=True)

    with col_viz2:
        st.subheader("🧪 Runner Types")
        df['unique_segments'] = df[valid_seg_cols].gt(0).sum(axis=1)
        
        scatter = alt.Chart(df).mark_circle(size=100).encode(
            x=alt.X('unique_segments:Q', title='Segments Attempted', scale=alt.Scale(domain=[0, len(valid_seg_cols)+1])),
            y=alt.Y('total_count:Q', title='Total Efforts'),
            color='name:N',
            tooltip=['name', 'total_count', 'unique_segments']
        ).properties(height=350).interactive()
        
        st.altair_chart(scatter, use_container_width=True)

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
            
            # --- 1. DEDUPLICATION LOGIC ---
            # Check if this user exists via ID (already connected)
            is_already_connected = False
            if not df_auth.empty and 'athlete_id' in df_auth.columns:
                 if ath['id'] in df_auth['athlete_id'].values:
                     is_already_connected = True
            
            if is_already_connected:
                st.warning("You are already connected!")
            else:
                # Check for "Scraped" version of this user
                if not df_auth.empty:
                    # Clean names in DB (remove " *")
                    df_auth['clean_name'] = df_auth['name'].astype(str).str.replace(" *", "").str.strip()
                    
                    # Find matching name that IS scraped
                    scraped_match = df_auth[
                        (df_auth['clean_name'] == new_full_name) & 
                        (df_auth['refresh_token'] == 'SCRAPED')
                    ]
                    
                    if not scraped_match.empty:
                        # Found a scraped duplicate! Delete it.
                        row_to_delete = scraped_match.index[0] + 2 # +2 for 1-based index and header
                        sheet.delete_rows(row_to_delete)
                        st.caption(f"Upgraded {new_full_name} from Scraped to Connected! 🟢")
                        time.sleep(1) # Let sheet update

                # Proceed to Register
                st.info("Scanning history... please wait.")
                start_epoch = int(CHALLENGE_START_DATE.timestamp())
                counts, last_epoch = fetch_efforts(data_json['access_token'], start_epoch)
                
                total = sum(counts.values())
                segment_values = [counts[sid] for sid in SEGMENT_IDS]
                
                new_row = [
                    ath['id'], 
                    new_full_name, # Name without *
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
                        # SKIP MANUAL USERS AND SCRAPED USERS
                        if row['refresh_token'] == "MANUAL" or row['refresh_token'] == "SCRAPED":
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