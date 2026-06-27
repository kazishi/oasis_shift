import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import streamlit as st
import pandas as pd
import re
import requests
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import AuthorizedSession, Request

from data_loader import ShiftCandidateGenerator, build_shift_categories, get_special_rules
from optimizer import build_and_solve_shift
from sheet_operations import (
    transfer_form_to_karioki,
    transfer_shifts_to_adjust,
    create_staff_sheet,
    update_output_conditional_format,
)
from validator import read_output_sheet, validate_shifts, summarize_issues
import config

st.set_page_config(
    page_title="OASIS シフト作成",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("🏠 OASIS シフト作成ツール")
st.caption("スプレッドシートのデータをもとに最適なシフトを自動生成します")
st.divider()

# ─── セッション状態の初期化 ───────────────────────────────────
_defaults = {
    "loaded": False,
    "prev_night_confirmed": False,
    "prev_night_adjustments": {},
    "generated": False,
    "staff_sheet_url": None,
    "df_possible": None,
    "df_master": None,
    "df_calendar": None,
    "df_staff": None,
    "staff_order": None,
    "date_map": None,
    "formatted_days": None,
    "staff_info_basic": None,
    "df_adjust_input": None,
    "df_result": None,
    "df_final": None,
    "report": None,

    "gc": None,
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _get_gc():
    if st.session_state.gc is None:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(
            config.SERVICE_ACCOUNT_FILE, scopes=scopes
        )
        # 社内プロキシの SSL インスペクションで TLS が切断されるため verify=False で回避
        token_session = requests.Session()
        token_session.verify = False
        auth_request = Request(session=token_session)

        authed_session = AuthorizedSession(creds, auth_request=auth_request)
        authed_session.verify = False

        gc = gspread.authorize(creds)
        gc.http_client.session = authed_session
        st.session_state.gc = gc
    return st.session_state.gc


def _fmt_date(s):
    m = re.search(r"(\d{1,2})[/-](\d{1,2})", str(s))
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}" if m else str(s)


def _show_error(e):
    st.error(f"❌ エラー：{e}")
    import traceback
    with st.expander("エラー詳細"):
        st.code(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════
# STEP 1　スプレッドシートを読み込む
# ═══════════════════════════════════════════════════════════════
st.subheader("Step 1　スプレッドシートを読み込む")

if st.button("📥 読み込む", type="primary"):
    for _k in list(_defaults.keys()):
        if _k == "gc":
            continue
        st.session_state[_k] = False if _k in ("loaded", "generated") else None

    with st.spinner("スプレッドシートに接続中..."):
        try:
            gen = ShiftCandidateGenerator()
            df_possible, df_master, df_calendar, df_staff, staff_order = gen.generate()

            days = df_calendar.iloc[:, 0].unique()
            date_map = {d: _fmt_date(d) for d in days}
            formatted_days = [date_map[d] for d in days]

            sib = df_staff[["氏名", "所属組織", "雇用", "管理者", "職種", "性別"]].drop_duplicates("氏名").copy()
            sib["_s"] = sib["氏名"].apply(lambda x: staff_order.index(x) if x in staff_order else 999)
            sib = sib.sort_values("_s").drop("_s", axis=1).reset_index(drop=True)

            st.session_state.update({
                "loaded": True,
                "df_possible": df_possible,
                "df_master": df_master,
                "df_calendar": df_calendar,
                "df_staff": df_staff,
                "staff_order": staff_order,
                "date_map": date_map,
                "formatted_days": formatted_days,
                "staff_info_basic": sib,
            })

        except Exception as e:
            _show_error(e)

if st.session_state.loaded:
    c1, c2, c3 = st.columns(3)
    c1.metric("👥 スタッフ数", f"{len(st.session_state.staff_order)} 名")
    c2.metric("📅 対象日数", f"{len(st.session_state.df_calendar)} 日")
    c3.metric("🔢 候補シフト数", f"{len(st.session_state.df_possible)} 件")
    days = st.session_state.formatted_days
    if days:
        st.caption(f"対象期間：{days[0]} 〜 {days[-1]}")

    # ─── Step 1-b: 前月最終日 夜勤者の確認 ──────────────────────
    st.divider()
    st.subheader("Step 1-b　前月最終日 夜勤者の確認")

    if not st.session_state.prev_night_confirmed:
        st.caption(
            "前月最終日に夜勤をしていたスタッフを最大5名まで選んでください。"
            "いない場合はそのまま「確認して次へ」を押してください。"
        )

        _special_rules = get_special_rules(st.session_state.df_staff)

        _selected_prev = st.multiselect(
            "前月最終日 夜勤者",
            options=st.session_state.staff_order,
            max_selections=5,
            key="prev_night_select",
            placeholder="スタッフを選択（いない場合は空のまま）",
        )

        _adjustments = {}
        for _name in _selected_prev:
            _rule = _special_rules.get(_name, "")
            if "LABO_0h" in _rule:
                _is_labo = st.checkbox(
                    f"　{_name}：前月末の夜勤は LABO 夜勤でした（LABO_0h 特例により +0h）",
                    value=True,
                    key=f"is_labo_{_name}",
                )
                _adjustments[_name] = 0 if _is_labo else 8
            else:
                st.write(f"　**{_name}**：換算に +8h 加算します")
                _adjustments[_name] = 8

        if st.button("✅ 確認して次へ", type="primary", key="prev_night_confirm_btn"):
            st.session_state.prev_night_adjustments = _adjustments
            st.session_state.prev_night_confirmed = True
            st.rerun()
    else:
        _adj = st.session_state.prev_night_adjustments
        if _adj:
            _parts = [f"**{n}**：+{h}h" for n, h in _adj.items()]
            st.info("前月末夜勤補正あり：" + "　".join(_parts))
        else:
            st.info("前月末夜勤者：なし")


# ═══════════════════════════════════════════════════════════════
# STEP 2　シフト希望を転記する
# ═══════════════════════════════════════════════════════════════
if st.session_state.prev_night_confirmed:
    st.divider()
    st.subheader("Step 2　シフト希望を転記する")
    st.caption("フォームの回答を仮置きへ反映したあと、調整用シートへ転記します")

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        if st.button("📋 フォーム → 仮置き転記"):
            with st.spinner("フォームの回答を読み込み中..."):
                try:
                    gc = _get_gc()
                    matched, days_n = transfer_form_to_karioki(
                        gc, st.session_state.df_calendar
                    )
                    st.success(f"✅ 転記完了（{matched} 名 / {days_n} 日分）")
                except Exception as e:
                    _show_error(e)

    with col_b:
        if st.button("📤 シフト希望 → 調整用（全部）"):
            with st.spinner("転記中..."):
                try:
                    gc = _get_gc()
                    transfer_shifts_to_adjust(gc, mode='all')
                    st.success("✅ 全項目を調整用シートへ転記しました")
                except Exception as e:
                    _show_error(e)

    with col_c:
        if st.button("📤 シフト希望 → 調整用（休みのみ）"):
            with st.spinner("転記中..."):
                try:
                    gc = _get_gc()
                    transfer_shifts_to_adjust(gc, mode='vacation_only')
                    st.success("✅「×」と「有給」のみ調整用シートへ転記しました")
                except Exception as e:
                    _show_error(e)


# ═══════════════════════════════════════════════════════════════
# STEP 3　調整内容を確認する
# ═══════════════════════════════════════════════════════════════
if st.session_state.prev_night_confirmed:
    st.divider()
    st.subheader("Step 3　調整内容を確認する")
    st.caption("調整用シートに入力がない場合はスキップして Step 4 に進めます")

    if st.button("🔍 調整用シートを確認する"):
        with st.spinner("読み込み中..."):
            try:
                gc = _get_gc()
                sh = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)
                ws_adj = sh.worksheet(config.ADJUST_SHEET_NAME)
                adj_data = ws_adj.get_all_values()
                formatted_days = st.session_state.formatted_days

                if len(adj_data) < 13:
                    st.session_state.df_adjust_input = pd.DataFrame()
                    st.info("ℹ️ 調整用シートに入力なし。全て自動配置します。")
                else:
                    df_adj = pd.DataFrame(adj_data[12:], columns=adj_data[11])
                    st.session_state.df_adjust_input = df_adj

                    issues = []
                    for _, row in df_adj.iterrows():
                        name = str(row.get("氏名", "")).strip()
                        if not name or name == "nan":
                            continue
                        for col in formatted_days:
                            if col not in row.index:
                                continue
                            val = str(row[col]).strip()
                            if val in ("", "nan", "None", "×", "-"):
                                continue
                            parts = [
                                x.strip() for x in re.split(r"[／/、,\s]", val)
                                if x.strip() not in ("", "-", "ー")
                            ]
                            if len(parts) > 1:
                                issues.append({"スタッフ": name, "日付": col, "内容": val})

                    fixed_count = sum(
                        1 for _, row in df_adj.iterrows()
                        for col in formatted_days
                        if col in row.index
                        and str(row[col]).strip() not in ("", "nan", "None")
                    )

                    if issues:
                        st.warning(
                            f"⚠️ 重複シフトが **{len(issues)} 件** あります。"
                            "実行前にスプレッドシートで修正してください。"
                        )
                        st.dataframe(pd.DataFrame(issues), use_container_width=True, hide_index=True)
                    else:
                        st.success(f"✅ 問題なし　（固定済み {fixed_count} コマ）")

                cats = build_shift_categories(st.session_state.df_master)
                with st.expander("📋 検出されたシフト区分（確認用）"):
                    tab_cats, tab_raw = st.tabs(["区分結果", "生データ確認"])
                    with tab_cats:
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**GARDEN**")
                            g_day = cats['g_day_nurses'] + cats['g_day_supports']
                            g_night = cats['g_night_nurses'] + cats['g_night_supports']
                            st.write("日勤:", g_day or "—")
                            st.write("夜勤:", g_night or "—")
                        with c2:
                            st.markdown("**LABO**")
                            l_day = cats['l_day_nurses'] + cats['l_day_supports']
                            l_night = cats['l_night_nurses'] + cats['l_night_supports']
                            st.write("日勤:", l_day or "—")
                            st.write("夜勤:", l_night or "—")
                    with tab_raw:
                        target = {'シフトNo', '所属', '勤務帯', '看護師資格'}
                        cols_idx = [i for i, c in enumerate(st.session_state.df_master.columns)
                                    if c in target]
                        st.dataframe(
                            st.session_state.df_master.iloc[:, cols_idx].drop_duplicates(),
                            use_container_width=True, hide_index=True
                        )

                rules = get_special_rules(st.session_state.df_staff)
                if rules:
                    st.info(f"⚙️ 特例ルール適用中：{rules}")

            except Exception as e:
                _show_error(e)


# ═══════════════════════════════════════════════════════════════
# STEP 4　シフトを生成する
# ═══════════════════════════════════════════════════════════════
if st.session_state.prev_night_confirmed:
    st.divider()
    st.subheader("Step 4　シフトを生成する")

    if st.button("⚡ シフト生成", type="primary"):
        # 調整シートを必ず最新状態で読み込む（Step 3 をスキップした場合にも対応）
        with st.spinner("調整シートを読み込み中..."):
            try:
                gc = _get_gc()
                sh = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)
                ws_adj = sh.worksheet(config.ADJUST_SHEET_NAME)
                adj_data = ws_adj.get_all_values()
                if len(adj_data) >= 13:
                    df_adj = pd.DataFrame(adj_data[12:], columns=adj_data[11])
                    st.session_state.df_adjust_input = df_adj
                else:
                    df_adj = pd.DataFrame()
            except Exception as e:
                st.warning(f"調整シートの読み込みに失敗しました（スキップ）: {e}")
                df_adj = st.session_state.df_adjust_input or pd.DataFrame()

        with st.spinner("最適化中... しばらくお待ちください（30秒〜1分）"):
            try:
                opt, df_result, report, _ = build_and_solve_shift(
                    st.session_state.df_possible,
                    st.session_state.df_master,
                    st.session_state.df_calendar,
                    st.session_state.df_staff,
                    df_adj,
                    st.session_state.date_map,
                    st.session_state.prev_night_adjustments,
                )

                staff_order    = st.session_state.staff_order
                formatted_days = st.session_state.formatted_days
                date_map       = st.session_state.date_map
                sib            = st.session_state.staff_info_basic
                df_possible    = st.session_state.df_possible
                df_staff_data  = st.session_state.df_staff
                days_list      = sorted(st.session_state.df_calendar.iloc[:, 0].unique())

                # ③ 後処理: 理事・サビ管・事務を◎日に直接上書き（LPに関係なく確実に配置）
                MANDATORY_ROLES = {'理事', 'サビ管', '事務'}
                override_rows = []
                override_log = []
                for _, stf in df_staff_data.iterrows():
                    mname = str(stf.get('氏名', '')).strip()
                    if not mname or mname == 'nan':
                        continue
                    shokushu = str(stf.get('職種', '')).strip()
                    if not any(kw in shokushu for kw in MANDATORY_ROLES):
                        continue
                    forced = []
                    for d in days_list:
                        if (mname, d) in opt.adj_fixed_points:
                            continue  # 調整シートで✕・希望休・指定シフトあり → スキップ
                        s5 = df_possible[
                            (df_possible['日付'] == d) &
                            (df_possible['氏名'].str.strip() == mname) &
                            (df_possible['スコア'] == 5)
                        ]
                        if s5.empty:
                            continue  # ◎なし → スキップ
                        shift_name = s5.iloc[0]['シフト名']
                        df_result = df_result[
                            ~((df_result['氏名'] == mname) & (df_result['日付'] == d))
                        ]
                        override_rows.append({'氏名': mname, '日付': d, 'シフト名': shift_name})
                        forced.append(f"{date_map.get(d, d)}({shift_name})")
                    override_log.append(
                        f"[{mname}/{shokushu}] 強制上書き {len(forced)}日: {forced}"
                    )
                if override_rows:
                    df_result = pd.concat(
                        [df_result, pd.DataFrame(override_rows)], ignore_index=True
                    )

                df_pivot = pd.DataFrame(index=staff_order, columns=formatted_days).fillna("×")
                if not df_result.empty:
                    for _, row in df_result.iterrows():
                        name = row["氏名"]
                        date = date_map.get(row["日付"])
                        if name in df_pivot.index and date in df_pivot.columns:
                            df_pivot.at[name, date] = row["シフト名"]

                df_final = pd.merge(
                    sib,
                    df_pivot.reset_index().rename(columns={"index": "氏名"}),
                    on="氏名",
                    how="inner",
                )
                df_final = df_final.sort_values(
                    "氏名",
                    key=lambda x: x.map({n: i for i, n in enumerate(staff_order)}),
                )

                st.session_state.df_result        = df_result
                st.session_state.report           = report
                st.session_state.df_final         = df_final
                st.session_state.generated        = True
                st.success("✅ シフト生成完了！")

            except Exception as e:
                _show_error(e)


# ═══════════════════════════════════════════════════════════════
# STEP 5　最適化結果を確認する
# ═══════════════════════════════════════════════════════════════
if st.session_state.generated:
    st.divider()
    st.subheader("Step 5　最適化結果を確認する")

    tab_shift, tab_report, tab_daily = st.tabs(["📋 シフト表", "📊 レポート", "📆 日別配置チェック"])

    with tab_shift:
        st.dataframe(
            st.session_state.df_final,
            use_container_width=True,
            height=520,
            hide_index=True,
        )

    with tab_report:
        report = st.session_state.report
        hours_lines, gender_lines = [], []
        in_gender = False
        for line in report:
            if "男女混在" in str(line):
                in_gender = True
            (gender_lines if in_gender else hours_lines).append(str(line))

        if hours_lines:
            st.markdown("##### 正社員 勤務時間")
            for line in hours_lines:
                if not line.strip():
                    continue
                if "基準比" in line:
                    if "+0.0" in line:
                        st.success(f"✅ {line}")
                    elif line.split("基準比")[-1].strip().startswith("+"):
                        st.info(f"🔵 {line}")
                    else:
                        st.error(f"🔴 {line}")
                else:
                    st.markdown(line)

        if gender_lines:
            st.markdown("---")
            st.markdown("##### 男女混在チェック")
            warnings = [l for l in gender_lines if "⚠️" in l]
            if warnings:
                for w in warnings:
                    st.warning(w)
            else:
                st.success("✅ 全日程で女性スタッフが配置されています")

    with tab_daily:
        st.caption("日ごとの必要人数との過不足・違反を確認します")
        try:
            shift_cats = build_shift_categories(st.session_state.df_master)
            issues = validate_shifts(
                st.session_state.df_final,
                st.session_state.df_calendar,
                shift_cats,
            )
            if not issues:
                st.success("✅ 配置エラーなし")
            else:
                df_issues = pd.DataFrame(issues)
                st.warning(f"⚠️ {len(issues)} 件のエラーがあります")
                # 種別ごとにフィルタ表示
                cats = df_issues['種別'].unique().tolist()
                selected = st.multiselect("種別フィルタ", cats, default=cats)
                st.dataframe(
                    df_issues[df_issues['種別'].isin(selected)],
                    use_container_width=True,
                    hide_index=True,
                )
        except Exception as e:
            _show_error(e)

    # ─────────────────────────────────────────────────────────
    # STEP 6　Outputシートに反映する
    # ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Step 6　Outputシートに反映する")
    st.info("「Output」シートの A12:AK50 が上書きされます。")

    if st.button("📤 Outputシートに反映する", type="primary"):
        with st.spinner("書き込み中..."):
            try:
                gc = _get_gc()
                sh = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)
                ws_out = sh.worksheet("Output")

                df_final = st.session_state.df_final
                ws_out.batch_clear(["A12:AK50"])
                ws_out.update(
                    range_name="A12",
                    values=[df_final.columns.values.tolist()]
                           + df_final.fillna("").values.tolist(),
                )

                # 行1〜10の条件付き書式をカレンダー目標値で更新
                update_output_conditional_format(
                    gc,
                    df_final,
                    st.session_state.df_calendar,
                    st.session_state.date_map,
                )

                st.success("✅ Outputシートへの書き込みが完了しました！")
                st.balloons()

            except Exception as e:
                _show_error(e)

    # ─────────────────────────────────────────────────────────
    # STEP 7　Output修正内容を検証する
    # ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Step 7　Output修正内容を検証する")
    st.caption("Outputシートを手動修正した後、ここで再検証できます")

    if st.button("🔎 Outputシートを読み込んで検証"):
        with st.spinner("Output シートを検証中..."):
            try:
                gc = _get_gc()
                df_check = read_output_sheet(gc)
                if df_check.empty:
                    st.warning("⚠️ Output シートにデータが見つかりませんでした")
                else:
                    shift_cats = build_shift_categories(st.session_state.df_master)
                    issues = validate_shifts(
                        df_check,
                        st.session_state.df_calendar,
                        shift_cats,
                    )
                    if not issues:
                        st.success("✅ エラーなし　スタッフ用シートへの出力に進めます")
                    else:
                        df_issues = pd.DataFrame(issues)
                        cats_list = df_issues['種別'].unique().tolist()
                        st.error(f"⚠️ {len(issues)} 件のエラーがあります")
                        selected = st.multiselect(
                            "種別フィルタ（Step 7）", cats_list, default=cats_list,
                            key="filter_step7"
                        )
                        st.dataframe(
                            df_issues[df_issues['種別'].isin(selected)],
                            use_container_width=True,
                            hide_index=True,
                        )
            except Exception as e:
                _show_error(e)

    # ─────────────────────────────────────────────────────────
    # STEP 8　スタッフ用シートに出力する
    # ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Step 8　スタッフ用シートに出力する")

    if st.button("🎨 スタッフ用シートを生成する", type="primary"):
        with st.spinner("スタッフ用シートを生成中...（色付け処理で少し時間がかかります）"):
            try:
                gc = _get_gc()
                url = create_staff_sheet(
                    gc,
                    st.session_state.df_final,
                    st.session_state.df_calendar,
                    st.session_state.date_map,
                )
                st.session_state.staff_sheet_url = url
                st.success("✅ スタッフ用シートの生成が完了しました！")
                st.balloons()
            except Exception as e:
                _show_error(e)

    # ─────────────────────────────────────────────────────────
    # STEP 9　シートを確認する
    # ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Step 9　シートを確認する")

    col_out, col_staff = st.columns(2)
    with col_out:
        st.link_button(
            "📊 Output シートを開く",
            config.OUTPUT_SPREADSHEET_URL,
            use_container_width=True,
        )
    with col_staff:
        staff_url = st.session_state.staff_sheet_url or config.OUTPUT_SPREADSHEET_URL
        st.link_button(
            "👥 スタッフ用シートを開く",
            staff_url,
            use_container_width=True,
            type="primary" if st.session_state.staff_sheet_url else "secondary",
        )
