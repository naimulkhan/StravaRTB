import streamlit as st
import pandas as pd
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- CONFIG ---
CHALLENGE_START_DATE = datetime(2024, 1, 1) # SET THIS to your challenge start date
SCOPES = ['read', 'activity:read_all']

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
            
            if not df.empty and ath['id'] in df['athlete_id'].values:
                st.warning("Already registered!")
            else:
                # Add new user. 
                # Start 'last_activity_epoch' at Challenge Start Date to trigger backfill on first sync
                start_epoch = int(CHALLENGE_START_DATE.timestamp())
                sheet.append_row([ath['id'], f"{ath['firstname']} {ath['lastname']}", data['refresh_token'], 0, start_epoch])
                st.success("You're in! Stats will update during the next daily sync.")
                st.query_params.clear()

# Display Leaderboard
data = sheet.get_all_records()
if data:
    df = pd.DataFrame(data)
    # Sort by total_count desc
    df = df.sort_values(by='total_count', ascending=False).reset_index(drop=True)
    st.dataframe(
        df[['name', 'total_count']],
        column_config={"name": "Runner", "total_count": st.column_config.NumberColumn("Efforts", format="%d ⚡")},
        use_container_width=True
    )
    st.caption(f"Syncs automatically every 24h. Last system update: {datetime.now().strftime('%H:%M UTC')}")