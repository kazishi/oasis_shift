import os

# --- 認証設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, 'google key', 'oasis-shift-app-473679a9583e.json')

# --- スプレッドシート設定 ---
SPREADSHEET_URLS = {
    'staff': 'https://docs.google.com/spreadsheets/d/1HOzHmgRtujDrJPdG4PhRNXeBh2EH1FNWPa6lGdx8BT8/edit',
    'calendar': 'https://docs.google.com/spreadsheets/d/1Y7qTDLdyyOD0Q0SbF9bYn8IC6K3q6_mQkx-7BmXGNQ0/edit',
    'master': 'https://docs.google.com/spreadsheets/d/1S-qmfFd2QjZV6jV4QRvtK4tca4LEZxgj2YSlzaX3vlo/edit',
    'form_responses': 'https://docs.google.com/spreadsheets/d/1FlIdaCD2qaCd-B1U7JkiJniJFdYTdAkK2WrrcLUdMv4/edit',
}

OUTPUT_SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1ILqfKTEL0_IWxwwMjBavACux4OkP44X-R9VRB4FIRdo/edit?usp=sharing"
DEFAULT_SHEET_NAME = 'Output'
ADJUST_SHEET_NAME = '調整用'

# --- 勤務ルール ---
SHIFT_CONSTRAINTS = {
    'MAX_CONSECUTIVE_WORK': 5,
    'BAN_NIGHT_TO_DAY': True
}