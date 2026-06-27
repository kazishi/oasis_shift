import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import config
import re

# 記号 → 内部スコアの対応表
# ◎=出勤指定(5)  △=出勤可能(3)  ✕=原則不可(除外)
_SYMBOL_SCORE = {'◎': 5, '△': 3}


def _parse_score(raw):
    """記号または旧数値スコアを整数に変換。除外すべき値は None を返す。
    ◎/△ 単体でも「◎出勤指定」「△出勤可能」などフルラベルでも受け付ける。"""
    s = str(raw).strip()
    for symbol, score in _SYMBOL_SCORE.items():
        if s == symbol or s.startswith(symbol):
            return score
    # 旧形式（数値）との後方互換：2以上のみ有効
    if s.isdigit() and int(s) > 1:
        return int(s)
    return None


class ShiftCandidateGenerator:
    def __init__(self):
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_file(
            config.SERVICE_ACCOUNT_FILE, scopes=scopes
        )
        self.gc = gspread.authorize(credentials)

    def _get_df_from_url(self, url):
        sh = self.gc.open_by_url(url)
        worksheet = sh.get_worksheet(0)
        raw_data = worksheet.get_all_values()
        header_idx = 0
        for i, row in enumerate(raw_data):
            if any(k in "".join(row) for k in ['氏名', '日付', 'シフトNo']):
                header_idx = i
                break

        # 重複列名・空列名を解消（2回目以降は _2, _3 と連番をつける）
        seen = {}
        headers = []
        for h in raw_data[header_idx]:
            h = h.strip()
            if not h:
                h = f'_unnamed_{len(headers)}'
            if h in seen:
                seen[h] += 1
                headers.append(f"{h}_{seen[h] + 1}")
            else:
                seen[h] = 1
                headers.append(h)

        return pd.DataFrame(
            raw_data[header_idx + 1:],
            columns=headers
        ).map(lambda x: str(x).strip())

    def generate(self):
        df_staff    = self._get_df_from_url(config.SPREADSHEET_URLS['staff'])
        df_calendar = self._get_df_from_url(config.SPREADSHEET_URLS['calendar'])
        df_master   = self._get_df_from_url(config.SPREADSHEET_URLS['master'])

        day_map = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}
        df_calendar['wd_idx'] = df_calendar.iloc[:, 1].apply(
            lambda x: day_map.get(str(x)[0], 0)
        )

        # 必要人数の読み込み
        def safe_int(val, default):
            return int(val) if str(val).isdigit() else default

        df_calendar['req_g_day']   = df_calendar.iloc[:, 5].apply(lambda x: safe_int(x, 3))
        df_calendar['req_l_day']   = df_calendar.iloc[:, 6].apply(lambda x: safe_int(x, 3))
        df_calendar['req_g_night'] = df_calendar.iloc[:, 7].apply(lambda x: safe_int(x, 1))
        df_calendar['req_l_night'] = df_calendar.iloc[:, 8].apply(lambda x: safe_int(x, 1))

        staff_order    = df_staff['氏名'].unique().tolist()
        possible       = []
        ignore_symbols = ['', '-', 'ー', '×']

        for _, cal in df_calendar.iterrows():
            d        = cal.iloc[0]
            wd_idx   = cal['wd_idx']
            is_kyujitsu = str(cal.iloc[4]) == '◯'

            for _, st in df_staff.iterrows():
                name = st.get('氏名')
                if not name or name == '氏名':
                    continue

                # ── 日勤 ──
                d_score_raw = st.iloc[11 + wd_idx]
                d_score = _parse_score(d_score_raw)
                if d_score is not None:
                    col = 9 if is_kyujitsu else 6
                    s_raw  = str(st.iloc[col])
                    s_list = [
                        x.strip() for x in re.split(r'[／/、,\s]', s_raw)
                        if x.strip() and x not in ignore_symbols
                    ]
                    # 段シフト（早朝・夕方分割）も追加
                    dan_raw  = str(st.iloc[8])
                    dan_list = [
                        x.strip() for x in re.split(r'[／/、,\s]', dan_raw)
                        if x.strip() and x not in ignore_symbols
                    ]
                    s_list.extend(dan_list)

                    for s in s_list:
                        # 優先度：スコア5 → -5000（最も選ばれやすい）
                        #         スコア2 → -2000（補充枠）
                        possible.append({
                            '氏名':   name,
                            '日付':   d,
                            'シフト名': s,
                            'スコア': d_score,
                            '優先度': -d_score * 1000
                        })

                # ── 夜勤 ──
                n_score_raw = st.iloc[18 + wd_idx]
                n_score = _parse_score(n_score_raw)
                if n_score is not None:
                    col = 10 if is_kyujitsu else 7
                    s_raw  = str(st.iloc[col])
                    s_list = [
                        x.strip() for x in re.split(r'[／/、,\s]', s_raw)
                        if x.strip() and x not in ignore_symbols
                    ]
                    for s in s_list:
                        possible.append({
                            '氏名':   name,
                            '日付':   d,
                            'シフト名': s,
                            'スコア': n_score,
                            '優先度': -n_score * 1000
                        })

        return pd.DataFrame(possible), df_master, df_calendar, df_staff, staff_order


def build_shift_categories(df_master):
    """マスターシートの所属・勤務帯・看護師資格列からシフト区分を動的に構築"""
    cats = {
        'g_day_nurses': [], 'g_day_supports': [],
        'l_day_nurses': [], 'l_day_supports': [],
        'g_night_nurses': [], 'g_night_supports': [],
        'l_night_nurses': [], 'l_night_supports': [],
        'night_shifts': [],
    }
    for _, row in df_master.iterrows():
        shift_no = str(row.get('シフトNo', '')).strip()
        if not shift_no or shift_no in ('', '-', 'ー', 'nan'):
            continue
        owner   = str(row.get('所属',      '')).strip().upper()
        wt      = str(row.get('勤務帯',    '')).strip()
        nursing = str(row.get('看護師資格', '')).strip()

        is_garden = 'GARDEN' in owner
        is_labo   = 'LABO'   in owner
        is_day    = '日勤' in wt
        is_night  = '夜勤' in wt or '当直' in wt
        is_nurse = nursing in {'◯', '○', '〇', '要', '必要', '1', 'TRUE'}

        if is_night:
            cats['night_shifts'].append(shift_no)

        if   is_garden and is_day:   cats['g_day_nurses'    if is_nurse else 'g_day_supports'  ].append(shift_no)
        elif is_garden and is_night: cats['g_night_nurses'  if is_nurse else 'g_night_supports'].append(shift_no)
        elif is_labo   and is_day:   cats['l_day_nurses'    if is_nurse else 'l_day_supports'  ].append(shift_no)
        elif is_labo   and is_night: cats['l_night_nurses'  if is_nurse else 'l_night_supports'].append(shift_no)

    return cats


def get_special_rules(df_staff):
    """職員名簿の「特例」列から個別ルールを取得。列がなければ空辞書を返す。
    例）特例列に「LABO_0h」と入力 → そのスタッフのLABO系シフトの換算時間が0hになる
    """
    if '特例' not in df_staff.columns:
        return {}
    rules = {}
    for _, row in df_staff.drop_duplicates('氏名').iterrows():
        name = str(row.get('氏名', '')).strip()
        rule = str(row.get('特例', '')).strip()
        if name and rule and rule not in ('nan', '-'):
            rules[name] = rule
    return rules
