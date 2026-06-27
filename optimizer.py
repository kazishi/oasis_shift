import re
import pulp
import pandas as pd
import config
from data_loader import build_shift_categories, get_special_rules


class OptimizationInfeasibleError(RuntimeError):
    pass


class ShiftOptimizer:
    def __init__(
        self,
        df_possible,
        df_master,
        df_calendar,
        df_staff,
        relax_requested_time_off=False,
        prev_night_adjustments=None,
    ):
        self.df_possible = df_possible.copy()
        self.df_master, self.df_calendar, self.df_staff = df_master, df_calendar, df_staff
        self.prob = pulp.LpProblem("Oasis_Feedback_Optimization", pulp.LpMinimize)
        self.x = {i: pulp.LpVariable(f"x_{i}", cat=pulp.LpBinary) for i in df_possible.index}
        self.violations, self.fixed_points = [], set()
        self.adj_fixed_points = set()  # 調整シートで明示的に指定された日（×・希望休・指定シフト）
        self.relax_requested_time_off = relax_requested_time_off
        self.requested_time_off = set()
        self.time_off_violation_vars = {}
        self.days = sorted(self.df_calendar.iloc[:, 0].unique())
        self.y = {name: {d: pulp.LpVariable(f"y_{name}_{d}", cat=pulp.LpBinary) for d in self.days} for name in
                  self.df_staff['氏名']}
        self.male_names = set(self.df_staff[self.df_staff['性別'] == '男性']['氏名'].tolist())
        self.female_names = set(self.df_staff[self.df_staff['性別'] == '女性']['氏名'].tolist())

        self.shift_cats    = build_shift_categories(df_master)
        self.special_rules = get_special_rules(df_staff)
        self.prev_night_adjustments = prev_night_adjustments or {}

    def _safe_float(self, val):
        try:
            s = str(val).replace(' ', '').replace('　', '')
            return float(s) if s and s not in ['-', 'ー', '×'] else 0.0
        except:
            return 0.0

    def _get_shift_hours(self, name, shift_name):
        if shift_name == "有給":
            return 8.0
        rule = self.special_rules.get(name, '')
        if rule == 'LABO_0h' and ('L' in shift_name or 'LABO' in shift_name.upper()):
            return 0.0
        matched = self.df_master[self.df_master['シフトNo'] == shift_name]
        if matched.empty:
            return 0.0
        return self._safe_float(matched['換算時間'].iloc[0])

    def _add_strict_balance_constraint(self, expression, target, weight_under, weight_over, label):
        v_under = pulp.LpVariable(f"v_under_{label}", lowBound=0)
        v_over = pulp.LpVariable(f"v_over_{label}", lowBound=0)
        self.prob += expression + v_under - v_over == target
        self.violations.append({'var': v_under, 'weight': weight_under, 'label': f"{label}_不足"})
        self.violations.append({'var': v_over, 'weight': weight_over, 'label': f"{label}_超過"})

    _GENERIC_LABELS = {'日勤', '夜勤', '段勤'}

    def _resolve_shift_label(self, name, real_date, label):
        """日勤/夜勤/段勤 をスタッフマスターの実シフトNo に変換する。
        変換できない場合は '' を返す（呼び出し側でスキップする）。"""
        if label not in self._GENERIC_LABELS:
            return label

        cal_row = self.df_calendar[self.df_calendar.iloc[:, 0] == real_date]
        is_kyujitsu = (not cal_row.empty) and (str(cal_row.iloc[0, 4]) == '◯')

        staff_row = self.df_staff[self.df_staff['氏名'] == name]
        if staff_row.empty:
            return ''
        st = staff_row.iloc[0]

        if label == '日勤':
            col = 9 if is_kyujitsu else 6
        elif label == '夜勤':
            col = 10 if is_kyujitsu else 7
        else:  # 段勤
            col = 8

        IGNORE = {'', '-', 'ー', '×', 'nan'}
        raw = str(st.iloc[col]).strip()
        if raw in IGNORE:
            return ''
        parts = [x.strip() for x in re.split(r'[／/、,\s]', raw)
                 if x.strip() and x.strip() not in IGNORE]
        return parts[0] if parts else ''

    # 固定役割として強制出勤扱いする職種キーワード
    _MANDATORY_ROLE_KEYWORDS = {'理事', 'サビ管', '事務'}

    def apply_mandatory_role_shifts(self):
        """理事・サビ管などの固定役割スタッフについて、
        職員名簿の ◎出勤指定（スコア5）がある日を自動で出勤割り当てする。
        戻り値: デバッグ情報の文字列リスト"""
        debug_lines = []
        for _, st in self.df_staff.iterrows():
            name = str(st.get('氏名', '')).strip()
            shokushu = str(st.get('職種', '')).strip()
            if not any(kw in shokushu for kw in self._MANDATORY_ROLE_KEYWORDS):
                continue
            forced_days = []
            skipped_days = []
            no_entry_days = []
            for d in self.days:
                if (name, d) in self.fixed_points:
                    skipped_days.append(d)
                    continue
                mask = (
                    (self.df_possible['日付'] == d) &
                    (self.df_possible['氏名'].str.strip() == name) &
                    (self.df_possible['スコア'] == 5)
                )
                score5_idxs = self.df_possible[mask].index.tolist()
                if not score5_idxs:
                    no_entry_days.append(d)
                    continue
                all_day_mask = (
                    (self.df_possible['日付'] == d) &
                    (self.df_possible['氏名'].str.strip() == name)
                )
                all_day_idxs = self.df_possible[all_day_mask].index.tolist()
                target_idx = score5_idxs[0]
                shift_name = self.df_possible.loc[target_idx, 'シフト名']
                self.prob += self.x[target_idx] == 1
                for idx in all_day_idxs:
                    if idx != target_idx:
                        self.prob += self.x[idx] == 0
                self.fixed_points.add((name, d))
                forced_days.append(f"{d}({shift_name})")
            debug_lines.append(
                f"[{name} / {shokushu}] "
                f"強制={len(forced_days)}日: {forced_days} | "
                f"スキップ(調整用)={len(skipped_days)}日 | "
                f"◎なし={len(no_entry_days)}日: {no_entry_days}"
            )
        return debug_lines

    def apply_fixed_shifts(self, df_adjust, date_map):
        if df_adjust is None or df_adjust.empty:
            return
        rev_date_map = {v: k for k, v in date_map.items()}
        for _, row in df_adjust.iterrows():
            name = str(row.get('氏名')).strip()
            if not name or name == 'nan':
                continue
            for fmt_date, val in row.items():
                if fmt_date not in rev_date_map:
                    continue
                cell_val = str(val).strip()
                if cell_val in ['', 'nan', 'None']:
                    continue
                real_date = rev_date_map[fmt_date]
                # 既に apply_mandatory_role_shifts などで固定済みの日はスキップ
                if (name, real_date) in self.fixed_points:
                    continue
                cell_val = self._resolve_shift_label(name, real_date, cell_val)
                if cell_val == '':
                    # ジェネリックラベルが翻訳不可（該当シフト未設定）→ スキップ
                    continue
                day_idxs = self.df_possible[
                    (self.df_possible['日付'] == real_date) & (self.df_possible['氏名'] == name)
                ].index.tolist()

                if cell_val == '×':
                    self.fixed_points.add((name, real_date))
                    self.adj_fixed_points.add((name, real_date))
                    for idx in day_idxs:
                        self.prob += self.x[idx] == 0
                elif cell_val == '希望休':
                    self.fixed_points.add((name, real_date))
                    self.adj_fixed_points.add((name, real_date))
                    self.requested_time_off.add((name, real_date))
                    if self.relax_requested_time_off:
                        violation = pulp.LpVariable(
                            f"v_time_off_{len(self.time_off_violation_vars)}",
                            cat=pulp.LpBinary,
                        )
                        self.time_off_violation_vars[(name, real_date)] = violation
                        self.prob += (
                            pulp.lpSum([self.x[idx] for idx in day_idxs])
                            <= violation
                        )
                        self.violations.append({
                            'var': violation,
                            'weight': 1_000_000_000_000,
                            'label': f"希望休違反_{name}_{real_date}",
                        })
                    else:
                        for idx in day_idxs:
                            self.prob += self.x[idx] == 0
                else:
                    self.fixed_points.add((name, real_date))
                    self.adj_fixed_points.add((name, real_date))
                    target_idxs = self.df_possible[
                        (self.df_possible['日付'] == real_date) &
                        (self.df_possible['氏名'] == name) &
                        (self.df_possible['シフト名'] == cell_val)
                    ].index.tolist()
                    if target_idxs:
                        self.prob += self.x[target_idxs[0]] == 1
                        for idx in day_idxs:
                            if idx != target_idxs[0]:
                                self.prob += self.x[idx] == 0
                    else:
                        new_idx = max(list(self.df_possible.index) + [-1]) + 1
                        self.df_possible.loc[new_idx] = {
                            '氏名': name, '日付': real_date, 'シフト名': cell_val,
                            'スコア': 0, '優先度': 0
                        }
                        self.x[new_idx] = pulp.LpVariable(f"x_{new_idx}", cat=pulp.LpBinary)
                        self.prob += self.x[new_idx] == 1
                        for idx in day_idxs:
                            self.prob += self.x[idx] == 0

    def solve(self):
        # シフト区分の定義（マスターシートから動的に取得）
        g_day_nurses     = self.shift_cats['g_day_nurses']
        g_day_supports   = self.shift_cats['g_day_supports']
        l_day_nurses     = self.shift_cats['l_day_nurses']
        l_day_supports   = self.shift_cats['l_day_supports']
        g_night_nurses   = self.shift_cats['g_night_nurses']
        g_night_supports = self.shift_cats['g_night_supports']
        l_night_nurses   = self.shift_cats['l_night_nurses']
        l_night_supports = self.shift_cats['l_night_supports']
        night_shifts     = set(self.shift_cats['night_shifts'])

        # GとL全体（男女混在チェック用）
        all_day_shifts   = g_day_nurses + g_day_supports + l_day_nurses + l_day_supports
        all_night_shifts = (
            g_night_nurses + g_night_supports
            + l_night_nurses + l_night_supports
        )

        # ──────────────────────────────────────────
        # 正社員の目標時間
        # ──────────────────────────────────────────
        target_hours_val = float(
            sum(1 for _, r in self.df_calendar.iterrows()
                if "土" not in str(r.iloc[1]) and "日" not in str(r.iloc[1])) * 8
        )

        # ──────────────────────────────────────────
        # ① 1人1日1シフト・夜勤翌日日勤禁止
        # ──────────────────────────────────────────
        for name in self.df_staff['氏名']:
            for idx, d in enumerate(self.days):
                curr_idxs = self.df_possible[
                    (self.df_possible['日付'] == d) & (self.df_possible['氏名'] == name)
                ].index
                self.prob += pulp.lpSum([self.x[i] for i in curr_idxs]) == self.y[name][d]
                self.prob += self.y[name][d] <= 1

                # 夜勤翌日の日勤禁止
                if (name, d) not in self.fixed_points and idx < len(self.days) - 1:
                    n_idxs = self.df_possible[
                        (self.df_possible['日付'] == d) &
                        (self.df_possible['氏名'] == name) &
                        (self.df_possible['シフト名'].isin(night_shifts))
                    ].index
                    next_d_idxs = self.df_possible[
                        (self.df_possible['日付'] == self.days[idx + 1]) &
                        (self.df_possible['氏名'] == name) &
                        (~self.df_possible['シフト名'].isin(night_shifts))
                    ].index
                    if n_idxs.any() and next_d_idxs.any():
                        self.prob += (
                            pulp.lpSum([self.x[i] for i in n_idxs]) +
                            pulp.lpSum([self.x[j] for j in next_d_idxs]) <= 1
                        )

        # ──────────────────────────────────────────
        # ①-b 最大連続勤務日数（y変数ベース・高ペナルティ）
        # x変数ベースだとシフト候補の名前不一致で制約が空になるリスクがあるため
        # y変数（その日働くか否かの 0/1）を直接使う。
        # ソフト制約にすることで LP infeasible 化を防ぎつつ、ペナルティで強制力を担保。
        # ──────────────────────────────────────────
        max_consec = config.SHIFT_CONSTRAINTS.get('MAX_CONSECUTIVE_WORK', 5)
        W_CONSEC = 10_000_000  # 夜勤不足(8M)より高く、絶対に避けさせる
        seen_consec = set()
        consec_var_idx = 0
        for name in self.df_staff['氏名']:
            if name in seen_consec or name not in self.y:
                continue
            seen_consec.add(name)
            for start_idx in range(len(self.days) - max_consec):
                window_days = self.days[start_idx: start_idx + max_consec + 1]
                v_c = pulp.LpVariable(f"v_c_{consec_var_idx}", lowBound=0)
                consec_var_idx += 1
                self.prob += (
                    pulp.lpSum([self.y[name][d] for d in window_days]) <= max_consec + v_c
                )
                self.violations.append({'var': v_c, 'weight': W_CONSEC, 'label': f"連勤_{name}"})

        # ──────────────────────────────────────────
        # ② 正社員の月間換算時間管理
        #    不足は厳禁（高ペナルティ）
        #    超過は最小限：シフト最小単位（日勤8h / 夜勤16h）未満に抑える
        # ──────────────────────────────────────────
        for name in self.df_staff[self.df_staff['雇用'] == '正社員']['氏名']:
            # 前月末夜勤者は翌月換算に加算分を差し引いた目標で計算
            prev_carry = self.prev_night_adjustments.get(name, 0)
            person_target = target_hours_val - prev_carry

            # その人が夜勤のみ可能かどうかで超過許容幅を決定
            possible_shifts = self.df_possible[self.df_possible['氏名'] == name]['シフト名'].unique()
            has_day_shift = any(s not in night_shifts for s in possible_shifts)
            # 夜勤のみの人は超過許容を16h未満、それ以外は8h未満とする
            # ペナルティで表現：超過1hあたりのコスト
            over_penalty = 500_000  # 超過への強いペナルティ（不足の1/2）

            p_vars = []
            for i in self.df_possible[self.df_possible['氏名'] == name].index:
                s = self.df_possible.loc[i, 'シフト名']
                h = self._get_shift_hours(name, s)
                p_vars.append(self.x[i] * h)

            v_s = pulp.LpVariable(f"v_s_{name}", lowBound=0)
            v_o = pulp.LpVariable(f"v_o_{name}", lowBound=0)
            self.prob += pulp.lpSum(p_vars) + v_s - v_o == person_target
            self.violations.append({'var': v_s, 'weight': 1_000_000, 'label': f"欠_{name}"})
            self.violations.append({'var': v_o, 'weight': over_penalty, 'label': f"超_{name}"})

        # ──────────────────────────────────────────
        # ③ 必要人数の充足（夜勤を最優先）
        #    w_u_n（夜勤不足）> w_u_d（日勤不足）>> w_ex（超過）
        # ──────────────────────────────────────────
        W_UNDER_NIGHT = 8_000_000   # 夜勤不足：最優先
        W_UNDER_DAY   = 1_500_000   # 日勤不足
        W_OVER        = 5_000_000   # 超過：配置過剰を強く抑制

        for d in self.days:
            cal = self.df_calendar[self.df_calendar.iloc[:, 0] == d].iloc[0]

            self._add_strict_balance_constraint(
                pulp.lpSum([self.x[i] for i in self.df_possible[
                    (self.df_possible['日付'] == d) &
                    (self.df_possible['シフト名'].isin(g_day_nurses + g_day_supports))
                ].index]),
                cal['req_g_day'], W_UNDER_DAY, W_OVER, f"G日_{d}"
            )
            self._add_strict_balance_constraint(
                pulp.lpSum([self.x[i] for i in self.df_possible[
                    (self.df_possible['日付'] == d) &
                    (self.df_possible['シフト名'].isin(l_day_nurses + l_day_supports))
                ].index]),
                cal['req_l_day'], W_UNDER_DAY, W_OVER, f"L日_{d}"
            )
            self._add_strict_balance_constraint(
                pulp.lpSum([self.x[i] for i in self.df_possible[
                    (self.df_possible['日付'] == d) &
                    (self.df_possible['シフト名'].isin(g_night_nurses + g_night_supports))
                ].index]),
                cal['req_g_night'], W_UNDER_NIGHT, W_OVER, f"G夜_{d}"
            )
            self._add_strict_balance_constraint(
                pulp.lpSum([self.x[i] for i in self.df_possible[
                    (self.df_possible['日付'] == d) &
                    (self.df_possible['シフト名'].isin(
                        l_night_nurses + l_night_supports
                    ))
                ].index]),
                cal['req_l_night'], W_UNDER_NIGHT, W_OVER, f"L夜_{d}"
            )

            # 看護師が最低1名いること（日勤・夜勤それぞれ）
            self.prob += pulp.lpSum([self.x[i] for i in self.df_possible[
                (self.df_possible['日付'] == d) &
                (self.df_possible['シフト名'].isin(g_day_nurses))
            ].index]) >= 1

            self.prob += pulp.lpSum([self.x[i] for i in self.df_possible[
                (self.df_possible['日付'] == d) &
                (self.df_possible['シフト名'].isin(g_night_nurses + l_night_nurses))
            ].index]) >= 1

        # ──────────────────────────────────────────
        # ④ 男性のみシフト禁止
        #    その日の全シフト（G+L合算、日勤・夜勤それぞれ）に
        #    女性が最低1人入る制約
        # ──────────────────────────────────────────
        for d in self.days:
            for shift_group, label in [
                (all_day_shifts,   f"男女混在_日勤_{d}"),
                (all_night_shifts, f"男女混在_夜勤_{d}"),
            ]:
                # その日のその時間帯に誰かアサインされるか確認
                total_assigned = pulp.lpSum([
                    self.x[i] for i in self.df_possible[
                        (self.df_possible['日付'] == d) &
                        (self.df_possible['シフト名'].isin(shift_group))
                    ].index
                ])

                # 女性のアサイン数
                female_assigned = pulp.lpSum([
                    self.x[i] for i in self.df_possible[
                        (self.df_possible['日付'] == d) &
                        (self.df_possible['シフト名'].isin(shift_group)) &
                        (self.df_possible['氏名'].isin(self.female_names))
                    ].index
                ])

                # 男性のアサイン数
                male_assigned = pulp.lpSum([
                    self.x[i] for i in self.df_possible[
                        (self.df_possible['日付'] == d) &
                        (self.df_possible['シフト名'].isin(shift_group)) &
                        (self.df_possible['氏名'].isin(self.male_names))
                    ].index
                ])

                # 誰かいる場合、女性が必ず1名以上いること
                # total_assigned が0の場合は制約を強制しない（大M法）
                M = 50  # 最大アサイン数より大きい値
                z = pulp.LpVariable(f"z_{label}", cat=pulp.LpBinary)
                # z=1 ↔ total_assigned >= 1
                self.prob += total_assigned <= M * z
                self.prob += total_assigned >= z
                # z=1 のとき female_assigned >= 1
                self.prob += female_assigned >= z

        # ──────────────────────────────────────────
        # 目的関数
        #   優先度コスト（◎は低コスト、△は高コスト）
        #   + 制約違反ペナルティ
        #   + 不要アサイン抑制（区分に関係なく出勤1件=100コスト）
        # ──────────────────────────────────────────
        # 区分に応じたアサインコスト：
        #   ◎出勤指定 → スコア5 → 優先度 -5000 → 寄与 -500（積極的に選ぶ）
        #   △出勤可能 → スコア3 → 優先度 -3000 → 寄与 -300
        # ＋ アサイン1件あたり100のコストを加えることで、
        #   △は -300+100 = -200（必要時のみ選ばれる）
        #   ◎は -500+100 = -400（積極的に選ばれる）
        self.prob += (
            pulp.lpSum([self.x[i] * self.df_possible.loc[i, '優先度'] * 0.1
                        for i in self.x.keys()]) +
            pulp.lpSum([v['var'] * v['weight'] for v in self.violations]) +
            pulp.lpSum([self.x[i] * 100 for i in self.x.keys()])
        )

        self.prob.solve(pulp.PULP_CBC_CMD(msg=0))
        status = pulp.LpStatus.get(self.prob.status, str(self.prob.status))
        if status != 'Optimal':
            raise OptimizationInfeasibleError(
                f"最適化に実行可能解がありません（solver status: {status}）"
            )

        # ──────────────────────────────────────────
        # レポート生成
        # ──────────────────────────────────────────
        times = []
        broken_time_off = [
            (name, date)
            for (name, date), variable in self.time_off_violation_vars.items()
            if pulp.value(variable) is not None and pulp.value(variable) > 0.5
        ]
        if self.relax_requested_time_off:
            times.append("【希望休の第二段階調整】")
            if broken_time_off:
                times.append(
                    "  厳密解が存在しないため、次の希望休だけを解除しました: "
                    + ", ".join(f"{name} {date}" for name, date in broken_time_off)
                )
            else:
                times.append("  希望休の解除なし")
            times.append("")
        for name in self.df_staff[self.df_staff['雇用'] == '正社員']['氏名']:
            prev_carry = self.prev_night_adjustments.get(name, 0)
            person_target = target_hours_val - prev_carry
            h_sum = sum(
                pulp.value(self.x[i]) * self._get_shift_hours(name, self.df_possible.loc[i, 'シフト名'])
                for i in self.df_possible[self.df_possible['氏名'] == name].index
            )
            carry_note = f" ※前月末夜勤 +{prev_carry}h" if prev_carry else ""
            times.append(f"{name:10}: {h_sum:5.1f}h (基準比 {h_sum - person_target:+.1f}h){carry_note}")

        # 男性のみシフト警告をレポートに追加
        times.append("")
        times.append("【男女混在チェック】")
        result_df = self.df_possible.loc[
            [i for i, v in self.x.items() if pulp.value(v) is not None and pulp.value(v) > 0.5],
            ['氏名', '日付', 'シフト名']
        ]
        for d in self.days:
            for label, shift_group in [("日勤", all_day_shifts), ("夜勤", all_night_shifts)]:
                day_result = result_df[
                    (result_df['日付'] == d) &
                    (result_df['シフト名'].isin(shift_group))
                ]
                if day_result.empty:
                    continue
                assigned_names = set(day_result['氏名'].tolist())
                has_female = bool(assigned_names & self.female_names)
                if not has_female and assigned_names:
                    times.append(f"  ⚠️ {d} {label}：女性スタッフなし → {sorted(assigned_names)}")

        return result_df, times


def build_and_solve_shift(
    df_possible,
    df_master,
    df_calendar,
    df_staff,
    df_adjust,
    date_map,
    prev_night_adjustments=None,
):
    """
    希望休を固定した厳密解を先に試し、解がない場合だけ第二段階へ進む。
    ×・有給・指定シフトは両段階ともハード制約のまま。
    """
    last_error = None
    for relax_requested_time_off in (False, True):
        optimizer = ShiftOptimizer(
            df_possible,
            df_master,
            df_calendar,
            df_staff,
            relax_requested_time_off=relax_requested_time_off,
            prev_night_adjustments=prev_night_adjustments,
        )
        optimizer.apply_fixed_shifts(df_adjust, date_map)
        mandatory_debug = optimizer.apply_mandatory_role_shifts()
        try:
            result, report = optimizer.solve()
            return optimizer, result, report, mandatory_debug
        except OptimizationInfeasibleError as exc:
            last_error = exc
            if relax_requested_time_off:
                raise
    raise last_error
