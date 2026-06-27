"""
Google Sheets への転記・スタッフ用シート生成
GAS スクリプト 4 本の Python 移植
"""
import re
import gspread
import config


# ─────────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────────

def _hex_to_color(hex_str):
    h = hex_str.lstrip('#')
    return {
        'red':   int(h[0:2], 16) / 255,
        'green': int(h[2:4], 16) / 255,
        'blue':  int(h[4:6], 16) / 255,
    }


def _cell_req(sheet_id, r0, r1, c0, c1, bg=None, fg=None):
    """repeatCell リクエストを生成するヘルパー"""
    fmt, fields = {}, []
    if bg:
        fmt['backgroundColor'] = _hex_to_color(bg)
        fields.append('userEnteredFormat.backgroundColor')
    if fg:
        fmt.setdefault('textFormat', {})['foregroundColor'] = _hex_to_color(fg)
        fields.append('userEnteredFormat.textFormat.foregroundColor')
    if not fields:
        return None
    return {
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': r0, 'endRowIndex': r1,
                'startColumnIndex': c0, 'endColumnIndex': c1,
            },
            'cell': {'userEnteredFormat': fmt},
            'fields': ','.join(fields),
        }
    }


# ─────────────────────────────────────────────────────────
# 4. Output シート 行1〜10 の条件付き書式をカレンダー目標値で更新
#    ※ 行1〜10 の値（COUNTIF数式）は一切変更しない
# ─────────────────────────────────────────────────────────

def update_output_conditional_format(gc, df_final, df_calendar, date_map):
    """
    Output シート行1〜10（G〜AK列）の条件付き書式を
    カレンダーの目標値に基づいて書き換える。

    仕組み:
      1. 目標値を行51〜55（非表示参照行）に書き込む
         Row51=G日目標, Row52=L日目標, Row53=G夜目標,
         Row54=L夜目標, Row55=夜合計目標
      2. 行1〜10 の既存 CF ルールを削除
      3. CUSTOM_FORMULA ルールを追加（列方向に相対参照）

    色ルール（目標値のある行3/6/8/9/10）:
      0人        : ダークレッド (#C00000) 白太文字  ← 最優先
      目標+2以上 : ピンク       (#EA9999)
      目標+1     : オレンジ     (#F9CB9C)
      目標ちょうど: 黄色        (#FFE599)
      目標-1     : 緑           (#B6D7A8)
      目標-2以下 : 青           (#9FC5E8)
    内訳行（1/2/4/5/7）: 0人のみダークレッド
    """
    sh       = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)
    ws       = sh.worksheet("Output")
    sheet_id = ws.id

    META      = {'氏名', '所属組織', '雇用', '管理者', '職種', '性別'}
    date_cols = [c for c in df_final.columns if c not in META]
    n_days    = len(date_cols)

    # ── 目標値リスト（日付順）を構築 ──────────────────
    target_map = {}
    for _, row in df_calendar.iterrows():
        fmt = date_map.get(row.iloc[0], str(row.iloc[0]))
        target_map[fmt] = {
            'g_day':   int(row.get('req_g_day',   0) or 0),
            'l_day':   int(row.get('req_l_day',   0) or 0),
            'g_night': int(row.get('req_g_night', 0) or 0),
            'l_night': int(row.get('req_l_night', 0) or 0),
        }

    gd  = [target_map.get(d, {}).get('g_day',   0) for d in date_cols]
    ld  = [target_map.get(d, {}).get('l_day',   0) for d in date_cols]
    gn  = [target_map.get(d, {}).get('g_night', 0) for d in date_cols]
    ln  = [target_map.get(d, {}).get('l_night', 0) for d in date_cols]
    nt  = [g + l for g, l in zip(gn, ln)]

    # ── シートが55行に満たない場合は拡張 ─────────────────
    if ws.row_count < 55:
        ws.resize(rows=55)

    # ── 参照用目標値を行51〜55 G列〜 に書き込む ────────
    # （条件付き書式数式から $51 行として参照される）
    ws.update(range_name='G51', values=[gd])
    ws.update(range_name='G52', values=[ld])
    ws.update(range_name='G53', values=[gn])
    ws.update(range_name='G54', values=[ln])
    ws.update(range_name='G55', values=[nt])

    # ── 既存 CF ルールのうち行1〜10 に関係するものを削除 ──
    metadata = sh.fetch_sheet_metadata()
    cur_sheet = next(
        (s for s in metadata.get('sheets', [])
         if s['properties']['sheetId'] == sheet_id),
        None
    )

    requests = []
    if cur_sheet:
        existing = cur_sheet.get('conditionalFormats', [])
        del_idx = []
        for idx, rule in enumerate(existing):
            for rng in rule.get('ranges', []):
                if (rng.get('sheetId') == sheet_id
                        and rng.get('startRowIndex', 99) < 10
                        and rng.get('startColumnIndex', 0) >= 6):
                    del_idx.append(idx)
                    break
        # 後ろから削除するとインデックスがずれない
        for idx in sorted(del_idx, reverse=True):
            requests.append({
                'deleteConditionalFormatRule': {'sheetId': sheet_id, 'index': idx}
            })

    # ── 新ルールを構築するヘルパー ────────────────────
    def _cf(row_0idx, formula, bg, fg='#000000', bold=False):
        """
        row_0idx : 0-indexed シート行
        formula  : G列アンカー（例 =G3=0）、列は相対参照なので横に展開される
        """
        return {
            'addConditionalFormatRule': {
                'rule': {
                    'ranges': [{
                        'sheetId':          sheet_id,
                        'startRowIndex':    row_0idx,
                        'endRowIndex':      row_0idx + 1,
                        'startColumnIndex': 6,            # G列
                        'endColumnIndex':   6 + n_days,
                    }],
                    'booleanRule': {
                        'condition': {
                            'type':   'CUSTOM_FORMULA',
                            'values': [{'userEnteredValue': formula}],
                        },
                        'format': {
                            'backgroundColor': _hex_to_color(bg),
                            'textFormat': {
                                'foregroundColor': _hex_to_color(fg),
                                'bold': bold,
                            },
                        },
                    },
                },
                'index': 0,   # 先頭挿入 → 最後に追加したものが最高優先度
            }
        }

    # 行(0-indexed) → 参照する目標行（シート行番号）
    # Row 3(idx=2)=G日→51, Row 6(idx=5)=L日→52,
    # Row 8(idx=7)=G夜→53, Row 9(idx=8)=L夜→54, Row 10(idx=9)=夜→55
    TARGET_REF = {2: 51, 5: 52, 7: 53, 8: 54, 9: 55}

    new_rules = []

    # 目標値あり行: 優先度低い順に追加（最後=index0 が最高優先度）
    for ri, ref_row in TARGET_REF.items():
        r = ri + 1  # 1-indexed 行番号（数式用）
        new_rules += [
            _cf(ri, f'=AND(G{r}<>0,G{r}<G${ref_row}-1)', '#9FC5E8'),        # 青: -2以下
            _cf(ri, f'=AND(G{r}<>0,G{r}=G${ref_row}-1)', '#B6D7A8'),        # 緑: -1
            _cf(ri, f'=AND(G{r}<>0,G{r}=G${ref_row})',   '#FFE599'),        # 黄: ちょうど
            _cf(ri, f'=AND(G{r}<>0,G{r}=G${ref_row}+1)', '#F9CB9C'),        # 橙: +1
            _cf(ri, f'=AND(G{r}<>0,G{r}>G${ref_row}+1)', '#EA9999'),        # 桃: +2以上
            _cf(ri, f'=G{r}=0',                           '#C00000', '#FFFFFF', True),  # 赤: 0人
        ]

    # 内訳行: 0人チェックのみ
    for ri in [0, 1, 3, 4, 6]:
        r = ri + 1
        new_rules.append(_cf(ri, f'=G{r}=0', '#C00000', '#FFFFFF', True))

    requests.extend(new_rules)

    if requests:
        sh.batch_update({'requests': requests})


# ─────────────────────────────────────────────────────────
# 1. フォーム回答 → 仮置き転記
#    GAS: remoteUpdateShifts()
# ─────────────────────────────────────────────────────────

def transfer_form_to_karioki(gc, df_calendar):
    """
    フォームの回答 1 の最新回答を名前照合で 仮置き シートへ転記する。
    戻り値: (転記したスタッフ数, 月の日数)
    """
    form_ss   = gc.open_by_url(config.SPREADSHEET_URLS['form_responses'])
    form_ws   = form_ss.worksheet('フォームの回答 1')
    karioki_ws = form_ss.worksheet('仮置き')

    days_in_month = len(df_calendar.iloc[:, 0].unique())
    form_data = form_ws.get_all_values()
    karioki_all = karioki_ws.get_all_values()
    if not form_data or len(karioki_all) < 4:
        return 0, days_in_month

    target_headers = karioki_all[2]
    target_date_headers = [
        header for header in target_headers if _normalize_date_header(header)
    ]
    source_col_by_date = _source_column_map(form_data[0], target_date_headers)
    source_extra_columns = {
        header: idx for idx, header in enumerate(form_data[0])
        if header in {'希望勤務回数', '備考'}
    }

    # 最新回答を辞書に登録（下から走査して初出のみ採用）
    latest_map = {}
    for row in reversed(form_data[1:]):
        if not row:
            continue
        name = re.sub(r'[\s　]', '', str(row[1]))
        if name and name not in latest_map:
            latest_map[name] = row

    # 未回答者は既存内容を消さず、回答がある人だけ更新する。
    batch, matched = [], 0
    for sheet_row, target_row in enumerate(karioki_all[3:], start=4):
        name = target_row[0] if target_row else ''
        source_row = latest_map.get(re.sub(r'[\s　]', '', str(name)))
        if not source_row:
            continue

        values = []
        for header in target_headers[1:]:
            target_date = _normalize_date_header(header)
            source_col = (
                source_col_by_date.get(target_date)
                if target_date
                else source_extra_columns.get(header)
            )
            values.append(
                source_row[source_col]
                if source_col is not None and source_col < len(source_row)
                else ''
            )

        end_col = _col_to_letter(1 + len(values))
        batch.append({
            'range': f'B{sheet_row}:{end_col}{sheet_row}',
            'values': [values],
        })
        matched += 1

    if batch:
        karioki_ws.batch_update(batch)

    return matched, days_in_month


# ─────────────────────────────────────────────────────────
# 2. シフト希望 → 調整用 転記（全部 or 休みのみ）
#    GAS: transferShiftData() / transferShiftDataOnlyXAndYukyu()
# ─────────────────────────────────────────────────────────

def _col_to_letter(col_1indexed):
    """1-indexed 列番号 → A1 列文字列変換 (例: 7→'G', 37→'AK')"""
    s = ''
    n = col_1indexed
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _normalize_date_header(value):
    """曜日などを含む見出しから MM/DD を取り出す。"""
    match = re.search(r'(\d{1,2})\s*[/\-]\s*(\d{1,2})', str(value))
    if not match:
        return None
    return f"{int(match.group(1)):02d}/{int(match.group(2)):02d}"


def _source_column_map(source_headers, target_date_headers):
    """転記元の日付列を転記先の日付へ対応付ける。"""
    target_dates = [_normalize_date_header(h) for h in target_date_headers]
    target_set = {date for date in target_dates if date}
    source_date_columns = [
        (idx, _normalize_date_header(header))
        for idx, header in enumerate(source_headers)
        if _normalize_date_header(header)
    ]

    mapping = {}
    used_targets = set()
    for position, (col_idx, normalized) in enumerate(source_date_columns):
        target_date = normalized if normalized in target_set else None
        if target_date in used_targets:
            target_date = None
        # 05/07 と 07/05 のような見出し誤記だけ、日付列内の位置で救済する。
        if target_date is None and position < len(target_dates):
            target_date = target_dates[position]
        if target_date:
            mapping[target_date] = col_idx
            used_targets.add(target_date)
    return mapping


def _build_adjust_transfer_updates(src_all, tgt_all, mode='all'):
    """調整用の日付セルだけに対する更新データを組み立てる。"""
    if len(src_all) < 2 or len(tgt_all) < 13:
        return []

    src_headers = src_all[0]
    tgt_headers = tgt_all[11]
    target_date_columns = [
        (idx, _normalize_date_header(header))
        for idx, header in enumerate(tgt_headers)
        if idx >= 6 and _normalize_date_header(header)
    ]
    if not target_date_columns:
        return []

    source_col_by_date = _source_column_map(
        src_headers,
        [tgt_headers[idx] for idx, _ in target_date_columns],
    )
    src_map = {}
    for row in src_all[1:]:
        if row and str(row[0]).strip():
            src_map[re.sub(r'[\s　]', '', str(row[0]))] = row

    keep = {'×', '有給', '希望休'}
    updates = []
    for sheet_row, row in enumerate(tgt_all[12:], start=13):
        name = row[0].strip() if row else ''
        source_row = src_map.get(re.sub(r'[\s　]', '', name))
        if not source_row:
            continue

        write_data = []
        for _, target_date in target_date_columns:
            source_col = source_col_by_date.get(target_date)
            value = (
                source_row[source_col]
                if source_col is not None and source_col < len(source_row)
                else ''
            )
            if mode == 'vacation_only' and value not in keep:
                value = ''
            write_data.append(value)

        start_col = target_date_columns[0][0] + 1
        end_col = target_date_columns[-1][0] + 1
        updates.append({
            'range': (
                f'{_col_to_letter(start_col)}{sheet_row}:'
                f'{_col_to_letter(end_col)}{sheet_row}'
            ),
            'values': [write_data],
        })
    return updates


def transfer_shifts_to_adjust(gc, mode='all'):
    """
    Output SS の「シフト希望」→「調整用」G列以降に転記する。
    位置（行番号）ではなく氏名照合で書き込むため、順番の違いによるずれが起きない。
    mode='all'          : すべての値をそのまま
    mode='vacation_only': 「×」「有給」「希望休」のみ、それ以外は空白
    """
    sh     = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)
    src_ws = sh.worksheet('シフト希望')
    tgt_ws = sh.worksheet(config.ADJUST_SHEET_NAME)

    # ── シフト希望: 全データ読み込み ────────────────────────
    # 構造: 行1=ヘッダー, 行2以降=スタッフ (A列=氏名, B列以降=日付別値)
    src_all = src_ws.get_all_values()
    if len(src_all) < 2:
        return

    # ── 調整用: 全データ読み込み ────────────────────────────
    # 行12=ヘッダー(A=氏名, G列以降=日付), 行13以降=スタッフデータ
    tgt_all = tgt_ws.get_all_values()
    if len(tgt_all) < 13:
        return

    batch = _build_adjust_transfer_updates(src_all, tgt_all, mode)

    if batch:
        tgt_ws.batch_update(batch)


# ─────────────────────────────────────────────────────────
# 3. スタッフ用シート生成
#    GAS: createStaffSheet()
# ─────────────────────────────────────────────────────────

def create_staff_sheet(gc, df_final, df_calendar, date_map):
    """
    df_final をもとに「スタッフ用」シートを色付きで生成する。
    戻り値: スタッフ用シートの URL
    """
    sh = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)

    # シートを準備（既存なら内容・書式をクリア）
    try:
        ws = sh.worksheet('スタッフ用')
        ws.clear()
        sh.batch_update({'requests': [{
            'updateCells': {
                'range': {'sheetId': ws.id},
                'fields': 'userEnteredFormat',
            }
        }]})
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet('スタッフ用', rows=100, cols=40)

    sheet_id = ws.id

    # ── メタ列 / 日付列の分離 ──────────────────────────
    META = ['氏名', '所属組織', '雇用', '管理者', '職種', '性別']
    date_cols = [c for c in df_final.columns if c not in META]
    n_dates = len(date_cols)

    # 曜日マップ（書式済み日付 → 曜日）
    weekday_map = {}
    for _, row in df_calendar.iterrows():
        fmt = date_map.get(row.iloc[0], str(row.iloc[0]))
        weekday_map[fmt] = str(row.iloc[1])

    # ── 1行目：日付 ──────────────────────────────────
    date_row = ['', '', '', '', ''] + list(date_cols)

    # ── 2行目：見出し＋曜日 ──────────────────────────
    header_row = ['氏名', '所属組織', '雇用', '職種', '性別'] + \
                 [weekday_map.get(d, '') for d in date_cols]

    # ── スタッフ行のデータ処理 ────────────────────────
    staff_data = []   # (display_row, kind_list, staff_meta)
    for _, row in df_final.iterrows():
        meta = [
            str(row.get('氏名',    '')),
            str(row.get('所属組織', '')),
            str(row.get('雇用',    '')),
            str(row.get('職種',    '')),
            str(row.get('性別',    '')),
        ]
        raw = [str(row.get(d, '')).strip() for d in date_cols]

        # 第1パス：シフト種別判定
        kinds = []
        for s in raw:
            if re.search(r'夜|当直', s):
                kinds.append('夜')
            elif re.search(r'日', s):
                kinds.append('日')
            elif re.search(r'段', s):
                kinds.append('段')
            elif s == '有給':
                kinds.append('有給')
            else:
                kinds.append('')

        # 第2パス：夜勤翌日処理
        display = ['有給' if k == '有給' else '' for k in kinds]
        for i, k in enumerate(kinds):
            if k == '夜' and i + 1 < n_dates:
                nk = kinds[i + 1]
                if nk == '夜':
                    display[i + 1] = '明入'
                # 翌日セルも青にするため kinds リストは維持

        staff_data.append((meta, raw, kinds, display))

    # ── 全値を一括書き込み ──────────────────────────
    all_values = [date_row, header_row]
    for meta, _, _, display in staff_data:
        all_values.append(meta + display)

    ws.update(range_name='A1', values=all_values)

    # ── フォーマットリクエスト構築 ────────────────────
    requests = []
    total_rows = len(all_values)
    total_cols = 5 + n_dates

    C_NIKKIN = '#F5A623'
    C_YAKKIN = '#4A90D9'
    C_DANKIN = '#7DB35A'
    C_GARDEN = '#D9EAD3'
    C_LABO   = '#FCE5CD'
    C_SEISHA = '#CFE2F3'
    C_NURSE  = '#FFF2CC'
    C_MALE   = '#D9EAD3'
    C_HEADER = '#CCCCCC'
    C_WEEKEND = '#888888'

    def add(r):
        if r:
            requests.append(r)

    # 2行目（index=1）: グレー背景
    add(_cell_req(sheet_id, 1, 2, 0, total_cols, bg=C_HEADER))

    # 1行目（index=0）: 土日列はダークグレー＋白文字
    for i, d in enumerate(date_cols):
        if weekday_map.get(d, '') in ('土', '日'):
            add(_cell_req(sheet_id, 0, 1, 5 + i, 6 + i, bg=C_WEEKEND, fg='#FFFFFF'))

    # スタッフ行
    for r_idx, (meta, _, kinds, _) in enumerate(staff_data):
        ri = 2 + r_idx  # 0-based row index

        # 所属（列B = index 1）
        belong = meta[1].strip()
        if belong == 'GARDEN':
            add(_cell_req(sheet_id, ri, ri+1, 1, 2, bg=C_GARDEN))
        elif belong == 'LABO':
            add(_cell_req(sheet_id, ri, ri+1, 1, 2, bg=C_LABO))

        # 雇用（列C = index 2）
        if meta[2].strip() == '正社員':
            add(_cell_req(sheet_id, ri, ri+1, 2, 3, bg=C_SEISHA))

        # 職種（列D = index 3）
        if meta[3].strip() == '看護師':
            add(_cell_req(sheet_id, ri, ri+1, 3, 4, bg=C_NURSE))

        # 性別（列E = index 4）
        if meta[4].strip() == '男性':
            add(_cell_req(sheet_id, ri, ri+1, 4, 5, bg=C_MALE))

        # シフト列
        for c_idx, k in enumerate(kinds):
            col = 5 + c_idx
            # 夜勤翌日セルも青
            is_yoru_yokujitsu = (
                c_idx > 0 and kinds[c_idx - 1] == '夜' and k != '夜'
            )
            if k == '夜' or is_yoru_yokujitsu:
                add(_cell_req(sheet_id, ri, ri+1, col, col+1, bg=C_YAKKIN))
            elif k == '日':
                add(_cell_req(sheet_id, ri, ri+1, col, col+1, bg=C_NIKKIN))
            elif k == '段':
                add(_cell_req(sheet_id, ri, ri+1, col, col+1, bg=C_DANKIN))
            elif k == '有給':
                add(_cell_req(sheet_id, ri, ri+1, col, col+1, bg='#FFFFFF', fg='#FF0000'))

    # 枠線
    requests.append({
        'updateBorders': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0, 'endRowIndex': total_rows,
                'startColumnIndex': 0, 'endColumnIndex': total_cols,
            },
            'top':    {'style': 'SOLID'},
            'bottom': {'style': 'SOLID'},
            'left':   {'style': 'SOLID'},
            'right':  {'style': 'SOLID'},
            'innerHorizontal': {'style': 'SOLID'},
            'innerVertical':   {'style': 'SOLID'},
        }
    })

    # 凡例
    legend_start = total_rows + 1  # 1行空けて
    ws.update(
        range_name=f'A{legend_start + 1}',
        values=[
            ['🟠 オレンジ：日勤'],
            ['🔵 青　　　：夜勤'],
            ['🟢 緑　　　：段勤'],
        ]
    )

    # 全フォーマットをまとめて送信
    if requests:
        sh.batch_update({'requests': requests})

    return f"https://docs.google.com/spreadsheets/d/{sh.id}#gid={sheet_id}"
