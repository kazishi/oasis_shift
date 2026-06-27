import unittest
import pandas as pd

from data_loader import build_shift_categories
from validator import validate_shifts
from sheet_operations import (
    _build_adjust_transfer_updates,
    _source_column_map,
)


class TransferTests(unittest.TestCase):
    def test_date_mapping_falls_back_for_malformed_headers(self):
        source = ["氏名", "07/01水", "05/07月", "12/7月", "希望勤務回数", "備考"]
        mapping = _source_column_map(source, ["07/01", "07/05", "07/12"])
        self.assertEqual(mapping, {"07/01": 1, "07/05": 2, "07/12": 3})

    def test_adjust_transfer_only_writes_date_columns(self):
        src = [
            ["氏名", "07/01水", "07/02木", "希望勤務回数", "備考"],
            ["藤井　朝子", "希望休", "日勤", "10回", "メモ"],
        ]
        tgt = [[""] for _ in range(11)] + [
            [
                "氏名", "所属組織", "雇用", "管理者", "職種", "性別",
                "07/01", "07/02", "換算合計", "不足時間",
            ],
            ["藤井 朝子", "", "", "", "", "", "", "", "=SUM(G13:H13)", "=0"],
        ]

        updates = _build_adjust_transfer_updates(src, tgt, mode="all")

        self.assertEqual(updates, [{
            "range": "G13:H13",
            "values": [["希望休", "日勤"]],
        }])

    def test_vacation_only_clears_non_vacation_values(self):
        src = [
            ["氏名", "07/01", "07/02", "07/03"],
            ["リダ", "日勤", "希望休", "有給"],
        ]
        tgt = [[""] for _ in range(11)] + [
            ["氏名", "", "", "", "", "", "07/01", "07/02", "07/03"],
            ["リダ", "", "", "", "", "", "手動", "手動", "手動"],
        ]

        updates = _build_adjust_transfer_updates(src, tgt, mode="vacation_only")

        self.assertEqual(updates[0]["values"], [["", "希望休", "有給"]])


class CategoryTests(unittest.TestCase):
    def test_circle_marks_nursing_shift(self):
        master = pd.DataFrame([{
            "シフトNo": "G日-看護1",
            "所属": "GARDEN",
            "勤務帯": "日勤",
            "看護師資格": "◯",
        }])

        categories = build_shift_categories(master)

        self.assertEqual(categories["g_day_nurses"], ["G日-看護1"])
        self.assertEqual(categories["g_day_supports"], [])


class ValidatorTests(unittest.TestCase):
    def test_calculation_columns_are_not_treated_as_dates(self):
        shifts = pd.DataFrame([{
            "氏名": "テスト",
            "性別": "女性",
            "07/01": "×",
            "換算合計": "184",
            "不足時間": "0",
        }])
        calendar = pd.DataFrame([["07/01", "水"]])
        categories = {
            "night_shifts": [],
            "g_day_nurses": [],
            "g_day_supports": [],
            "l_day_nurses": [],
            "l_day_supports": [],
            "g_night_nurses": [],
            "g_night_supports": [],
            "l_night_nurses": [],
            "l_night_supports": [],
        }

        issues = validate_shifts(shifts, calendar, categories)

        self.assertFalse(any("換算合計" in issue["内容"] for issue in issues))
        self.assertFalse(any("不足時間" in issue["内容"] for issue in issues))


if __name__ == "__main__":
    unittest.main()
