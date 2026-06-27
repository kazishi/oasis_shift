import os
import pandas as pd
from data_loader import ShiftCandidateGenerator
from optimizer import build_and_solve_shift
import config
import re
import gspread
from google.oauth2.service_account import Credentials


def main():
    print("\n--- Oasis Shift System Start ---")
    try:
        # 1. データ準備と認証
        gen = ShiftCandidateGenerator()
        df_possible, df_master, df_calendar, df_staff, staff_order = gen.generate()

        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_file(config.SERVICE_ACCOUNT_FILE, scopes=scopes)
        gc = gspread.authorize(credentials)
        sh = gc.open_by_url(config.OUTPUT_SPREADSHEET_URL)

        # 2. 日付・スタッフ情報の整理
        days = df_calendar.iloc[:, 0].unique()

        def fmt_d(s):
            m = re.search(r'(\d{1,2})[/-](\d{1,2})', str(s))
            return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}" if m else str(s)

        date_map = {d: fmt_d(d) for d in days}
        formatted_days = [date_map[d] for d in days]

        # スタッフ情報の列名は「安定版」に準拠
        staff_info_basic = df_staff[['氏名', '所属組織', '雇用', '管理者', '職種', '性別']].drop_duplicates('氏名')
        staff_info_basic['sort_idx'] = staff_info_basic['氏名'].apply(
            lambda x: staff_order.index(x) if x in staff_order else 999)
        staff_info_basic = staff_info_basic.sort_values('sort_idx').drop('sort_idx', axis=1)

        # 3. 調整用シートの処理
        ws_adj = sh.worksheet(config.ADJUST_SHEET_NAME)
        adj_data = ws_adj.get_all_values()

        # 枠がない場合は初期作成（ここも50行制限に合わせて修正）
        if len(adj_data) < 13:
            print("調整用シートに枠を作成します...")
            empty_days = pd.DataFrame('', index=staff_info_basic.index, columns=formatted_days)
            adj_init_df = pd.concat([staff_info_basic, empty_days], axis=1)
            ws_adj.batch_clear(["A12:AK50"])
            ws_adj.update(range_name="A12", values=[adj_init_df.columns.values.tolist()] + adj_init_df.values.tolist())
            df_adjust_input = pd.DataFrame()
        else:
            df_adjust_input = pd.DataFrame(adj_data[12:], columns=adj_data[11])

        # 4. 最適化実行
        opt, df_result, report, mandatory_debug = build_and_solve_shift(
            df_possible,
            df_master,
            df_calendar,
            df_staff,
            df_adjust_input,
            date_map,
        )

        # 4.5 例外ルール: 理事・サビ管・事務は◎日に後処理で直接上書き
        #     （調整シートで明示的に指定された日のみ除外）
        MANDATORY_ROLES = {'理事', 'サビ管', '事務'}
        override_rows = []
        override_log = []
        for _, st in df_staff.iterrows():
            name = str(st.get('氏名', '')).strip()
            if not name or name == 'nan':
                continue
            shokushu = str(st.get('職種', '')).strip()
            if not any(kw in shokushu for kw in MANDATORY_ROLES):
                continue
            forced = []
            for d in days:
                if (name, d) in opt.adj_fixed_points:
                    continue  # 調整シートで✕・希望休・指定シフトあり → スキップ
                score5 = df_possible[
                    (df_possible['日付'] == d) &
                    (df_possible['氏名'].str.strip() == name) &
                    (df_possible['スコア'] == 5)
                ]
                if score5.empty:
                    continue  # ◎なし → スキップ
                shift_name = score5.iloc[0]['シフト名']
                df_result = df_result[~((df_result['氏名'] == name) & (df_result['日付'] == d))]
                override_rows.append({'氏名': name, '日付': d, 'シフト名': shift_name})
                forced.append(f"{date_map.get(d, d)}({shift_name})")
            override_log.append(f"[{name}/{shokushu}] 強制上書き {len(forced)}日: {forced}")
        if override_rows:
            df_result = pd.concat([df_result, pd.DataFrame(override_rows)], ignore_index=True)

        # 5. Outputシートへの書き込み準備
        df_pivot = pd.DataFrame(index=staff_order, columns=formatted_days).fillna('×')
        if not df_result.empty:
            for _, row in df_result.iterrows():
                name, date = row['氏名'], date_map.get(row['日付'])
                if name in df_pivot.index and date in df_pivot.columns:
                    df_pivot.at[name, date] = row['シフト名']

        df_final = pd.merge(staff_info_basic, df_pivot.reset_index().rename(columns={'index': '氏名'}), on='氏名',
                            how='inner')
        df_final = df_final.sort_values('氏名', key=lambda x: x.map({name: i for i, name in enumerate(staff_order)}))

        # 6. Outputシートへの反映
        ws_out = sh.worksheet("Output")

        # 【修正箇所】クリア範囲を A12:AK50 に限定（51行目以降を保護）
        ws_out.batch_clear(["A12:AK50"])

        # データの書き込み（ヘッダーを含む）
        ws_out.update(range_name="A12", values=[df_final.columns.values.tolist()] + df_final.values.tolist())

        print("スプレッドシートの書き込みが完了しました。")
        print("\n" + "=" * 40 + "\n  強制上書きログ（理事・サビ管・事務）\n" + "=" * 40)
        for line in override_log:
            print(line)
        print("\n" + "=" * 40 + "\n      シフト調整レポート\n" + "=" * 40)
        for line in report:
            print(line)
        print("=" * 40)

    except Exception as e:
        print(f"実行エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
