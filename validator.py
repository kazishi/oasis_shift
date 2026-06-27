"""
validator.py - シフト表のルール違反チェック
Output シート書き込み後の再検証にも使用
"""
import re
import pandas as pd
import config


META_COLS = {'氏名', '所属組織', '雇用', '管理者', '職種', '性別'}

_MAX_CONSEC = config.SHIFT_CONSTRAINTS.get('MAX_CONSECUTIVE_WORK', 5)


def read_output_sheet(gc):
    """
    Output シートを読み込んで df_final 相当の DataFrame を返す。
    (ユーザーが手動修正した後に呼ぶ用)
    """
    sh   = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)
    ws   = sh.worksheet('Output')
    data = ws.get_all_values()

    # app.py は A12 にヘッダーを書く（0-indexed: row 11）
    if len(data) < 13:
        return pd.DataFrame()

    headers = data[11]
    rows    = [r for r in data[12:] if any(v.strip() for v in r)]
    if not rows:
        return pd.DataFrame()

    max_cols = max(len(headers), max(len(r) for r in rows))
    headers  = list(headers) + [''] * (max_cols - len(headers))
    rows     = [r + [''] * (max_cols - len(r)) for r in rows]

    df = pd.DataFrame(rows, columns=headers)
    # 空名前の列を除去
    df = df.loc[:, [c for c in df.columns if str(c).strip()]]
    return df


def validate_shifts(df, df_calendar, shift_cats):
    """
    シフト表 (df_final 形式) を検証してエラー一覧を返す。

    引数:
        df          : df_final 相当 (氏名, 所属組織, ..., 日付列...)
        df_calendar : 稼働日カレンダー DataFrame
        shift_cats  : build_shift_categories() の戻り値

    戻り値:
        list[dict] 各要素は {'種別', 'スタッフ or 日付', '内容'}
    """
    if df is None or df.empty:
        return []

    issues = []

    date_cols = [
        c for c in df.columns
        if re.fullmatch(r'\d{2}/\d{2}', str(c).strip())
    ]
    night_set  = set(shift_cats['night_shifts'])
    g_day      = set(shift_cats['g_day_nurses'] + shift_cats['g_day_supports'])
    l_day      = set(shift_cats['l_day_nurses'] + shift_cats['l_day_supports'])
    g_night    = set(shift_cats['g_night_nurses'] + shift_cats['g_night_supports'])
    l_night    = set(shift_cats['l_night_nurses'] + shift_cats['l_night_supports'])
    all_shifts = g_day | l_day | g_night | l_night

    # 日付列 → カレンダー行のマッピング
    def _fmt(s):
        m = re.search(r'(\d{1,2})[/-](\d{1,2})', str(s))
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}" if m else str(s)

    cal_by_fmt = {}
    for _, row in df_calendar.iterrows():
        cal_by_fmt[_fmt(row.iloc[0])] = row

    female_names = set(df[df['性別'] == '女性']['氏名'].tolist())

    # ── 正社員の月間目標時間 ────────────────────────────────
    target_h = float(
        sum(1 for _, r in df_calendar.iterrows()
            if '土' not in str(r.iloc[1]) and '日' not in str(r.iloc[1])) * 8
    )

    def _is_work(v):
        return str(v).strip() not in ('×', '', 'nan', 'None', '-')

    def _shift_hours(shift_name):
        if shift_name == '有給':
            return 8.0
        try:
            from data_loader import ShiftCandidateGenerator
        except Exception:
            return 8.0
        return 0.0  # 簡易版：マスター参照なし

    # ── 個人別チェック ──────────────────────────────────
    for _, row in df.iterrows():
        name   = str(row.get('氏名', '')).strip()
        employ = str(row.get('雇用', '')).strip()
        if not name or name == '氏名':
            continue

        shifts = [str(row.get(d, '')).strip() for d in date_cols]

        # 連続勤務日数
        consec = 0
        for i, s in enumerate(shifts):
            if _is_work(s):
                consec += 1
                if consec > _MAX_CONSEC:
                    issues.append({
                        '種別': '連続勤務超過',
                        '対象': name,
                        '内容': f'{date_cols[i]} 連続 {consec} 日',
                    })
            else:
                consec = 0

        # 夜勤翌日日勤禁止
        for i in range(len(shifts) - 1):
            if shifts[i] in night_set:
                nxt = shifts[i + 1]
                if _is_work(nxt) and nxt not in night_set:
                    issues.append({
                        '種別': '夜勤翌日日勤',
                        '対象': name,
                        '内容': f'{date_cols[i]} 夜勤 → {date_cols[i+1]} 日勤',
                    })

    # ── 日別チェック ──────────────────────────────────
    for d in date_cols:
        cal = cal_by_fmt.get(d)
        if cal is None:
            continue

        day_shifts = df[['氏名', '性別', d]].copy()
        day_shifts.columns = ['氏名', '性別', 'シフト']
        day_shifts['シフト'] = day_shifts['シフト'].astype(str).str.strip()
        day_shifts = day_shifts[day_shifts['シフト'].apply(_is_work)]

        def count_in(shift_set):
            return day_shifts['シフト'].isin(shift_set).sum()

        def names_in(shift_set):
            return day_shifts[day_shifts['シフト'].isin(shift_set)]['氏名'].tolist()

        req_gd = int(cal.get('req_g_day',   0) or 0)
        req_ld = int(cal.get('req_l_day',   0) or 0)
        req_gn = int(cal.get('req_g_night', 0) or 0)
        req_ln = int(cal.get('req_l_night', 0) or 0)

        for label, shift_set, req in [
            ('GARDEN日勤', g_day,   req_gd),
            ('LABO日勤',   l_day,   req_ld),
            ('GARDEN夜勤', g_night, req_gn),
            ('LABO夜勤',   l_night, req_ln),
        ]:
            cnt = count_in(shift_set)
            if cnt < req:
                issues.append({
                    '種別': '人数不足',
                    '対象': d,
                    '内容': f'{label}: {cnt} 人 / 必要 {req} 人',
                })
            elif cnt > req:
                issues.append({
                    '種別': '人数超過',
                    '対象': d,
                    '内容': f'{label}: {cnt} 人 / 必要 {req} 人',
                })

        # 女性スタッフ不在チェック
        for label, shift_set in [('日勤', g_day | l_day), ('夜勤', g_night | l_night)]:
            assigned = day_shifts[day_shifts['シフト'].isin(shift_set)]
            if assigned.empty:
                continue
            has_female = assigned['性別'].eq('女性').any()
            if not has_female:
                names = assigned['氏名'].tolist()
                issues.append({
                    '種別': '男女混在',
                    '対象': d,
                    '内容': f'{label} に女性スタッフなし → {names}',
                })

    return issues


def summarize_issues(issues):
    """issue リストをカテゴリ別に集計して文字列リストで返す"""
    if not issues:
        return ['✅ エラーなし']

    lines = []
    from collections import Counter
    cats = Counter(i['種別'] for i in issues)
    lines.append(f"⚠️ 合計 {len(issues)} 件のエラーが見つかりました")
    for cat, n in cats.items():
        lines.append(f"  {cat}: {n} 件")
    return lines
