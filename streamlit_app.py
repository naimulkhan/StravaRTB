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
    
    # Check if headers match what is in the sheet
    try:
        current_headers = sheet.row_values(1)
    except:
        current_headers = []

    # If sheet is empty OR headers are old/wrong, force update Row 1
    if not current_headers or current_headers != headers:
        # This overwrites just the header row (Row 1) without touching data below
        sheet.update(range_name='A1', values=[headers])

def update_last_edit():
    """Saves the current Eastern Time to the Metadata tab."""
    try:
        ws = sh.worksheet("Metadata")
    except:
        ws = sh.add_worksheet(title="Metadata", rows=5, cols=2)
    
    # Save formatted Eastern Time
    now_et = pd.Timestamp.now('America/Toronto').strftime('%Y-%m-%d %I:%M %p ET')
    ws.update_acell('A1', now_et)

def get_last_edit_time():
    """Reads the timestamp from the Metadata tab."""
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
st.set_page_config(page_title="Run The Beaches Toronto!", page_icon="🏃")

# Ensure headers are correct before loading data
init_db(sheet)

# --- HEADER & WINNER ---
st.markdown("<h1 style='text-align: center;'>🏃 Run The Beaches Toronto Segment Challenge</h1>", unsafe_allow_html=True)

data = sheet.get_all_records()
df = pd.DataFrame(data)

if not df.empty:
    # 1. CLEAN DATA IMMEDIATELY (Fixes charts and leaderboards)
    df.columns = df.columns.astype(str).str.strip()
    
    # Force all segment columns to be integers (converts "" to 0)
    for seg_name in SEGMENTS.values():
        if seg_name in df.columns:
            df[seg_name] = pd.to_numeric(df[seg_name], errors='coerce').fillna(0).astype(int)

    # 2. VISUALIZATION SECTION
    st.divider()
    st.header("📊 Race Analysis")
    
    # Strategy: Who should I chase?
    with st.expander("🎯 Strategy: Who should I chase?", expanded=True):
        runner_list = df['name'].tolist()
        me = st.selectbox("I am...", runner_list, index=0)
        my_row = df[df['name'] == me].iloc[0]
        targets = []
        
        for seg_name in SEGMENTS.values():
            if seg_name in df.columns:
                current_leader_val = df[seg_name].max()
                my_val = my_row[seg_name]
                
                if my_val < current_leader_val:
                    gap = current_leader_val - my_val
                    if gap <= 5:
                        targets.append((seg_name, gap, current_leader_val))
        
        if targets:
            st.write(f"**{me}**, you are close to the lead on these segments:")
            cols = st.columns(len(targets) if len(targets) < 4 else 3)
            for i, (seg, gap, leader_val) in enumerate(targets):
                with cols[i % 3]:
                    st.metric(
                        label=seg, 
                        value=f"{int(my_row[seg])} efforts", 
                        delta=f"{int(gap)} to tie 1st",
                        delta_color="normal"
                    )
        else:
            st.success("You are either leading everything or too far behind to catch up quickly! Keep pushing.")

    col_viz1, col_viz2 = st.columns(2)

    # Heatmap
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
            ).properties(height=400)
            
            st.altair_chart(c, use_container_width=True)

    # Scatter Plot
    with col_viz2:
        st.subheader("🧪 Runner Archetypes")
        df['unique_segments'] = df[valid_seg_cols].gt(0).sum(axis=1)
        
        scatter = alt.Chart(df).mark_circle(size=100).encode(
            x=alt.X('unique_segments:Q', title='Different Segments Attempted', scale=alt.Scale(domain=[0, len(valid_seg_cols)+1])),
            y=alt.Y('total_count:Q', title='Total Efforts'),
            color='name:N',
            tooltip=['name', 'total_count', 'unique_segments']
        ).properties(height=400).interactive()
        
        st.altair_chart(scatter, use_container_width=True)
        st.caption("Top Right = High Volume & High Variety. Top Left = Obsessed with one hill.")

    st.divider()

    # 3. CALCULATE LEADERS (Data is already clean)
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
        st.info(f"👑 **Current Leader:** {', '.join(champions)} ({max_wins} Segments Won)")
    else:
        st.info("👑 Current Leader: None yet!")
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

# --- DISPLAY LEADERBOARDS ---
# --- DISPLAY LEADERBOARDS ---
if data:
    df_disp = pd.DataFrame(data)
    
    # --- FIX: SANITIZE DATA BEFORE SORTING ---
    # 1. Fix column names
    df_disp.columns = df_disp.columns.astype(str).str.strip()
    
    # 2. Force all segment columns to be integers (converts "" to 0)
    for seg_name in SEGMENTS.values():
        if seg_name in df_disp.columns:
            df_disp[seg_name] = pd.to_numeric(df_disp[seg_name], errors='coerce').fillna(0).astype(int)
    # -----------------------------------------

    if list(SEGMENTS.values())[0] in df_disp.columns:
        tabs = st.tabs(list(SEGMENTS.values()))
        
        for i, seg_name in enumerate(SEGMENTS.values()):
            with tabs[i]:
                # Now sort_values will work because we forced the column to be integers above
                seg_df = df_disp[['name', seg_name]].sort_values(by=seg_name, ascending=False).reset_index(drop=True)
                seg_df = seg_df[seg_df[seg_name] > 0] 
                
                if not seg_df.empty:
                    display_df = seg_df.copy()
                    
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
                    st.caption("No efforts recorded.")
    else:
        st.info("Database empty.")
else:
    st.info("No runners yet.")

st.divider()
st.caption(f"Last system update: {get_last_edit_time()}")