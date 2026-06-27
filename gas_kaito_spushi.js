/**
 * OASIS シフトフォーム 月次更新スクリプト（解凍用スプシ版）
 * バージョン: 2026-05-30
 * 設置場所: 解凍用スプシ（Google スプレッドシート のスクリプトエディタ）
 *
 * ★ 前バージョンとの重要な差異 ★
 * 1. setDestination 前に既存「フォームの回答*」シートを全て __OLD__ リネーム
 *    → Forms が必ず「新規シート」を作成するよう強制
 * 2. setDestination 後にヘッダー行（1行目）を絶対に変更しない
 *    → ヘッダーを消すと Forms がバックグラウンドで列を再追記するため
 * 3. データ行（2行目以降）のみクリア
 */

// ==================== 設定 ====================
var FORM_ID                   = '1v5MjMhicn9LC9KsspLRWLF9w_mG7HVxFrKXFIW6NrHs';
var RESPONSE_SPREADSHEET_ID   = '1FlIdaCD2qaCd-B1U7JkiJniJFdYTdAkK2WrrcLUdMv4';
var CALENDAR_SPREADSHEET_ID   = '1Y7qTDLdyyOD0Q0SbF9bYn8IC6K3q6_mQkx-7BmXGNQ0';

var CALENDAR_SHEET_NAME       = 'シート1';
var LOCAL_CALENDAR_SHEET_NAME = 'カレンダー';
var STAFF_SHEET_NAME          = '職員名簿';
var RESPONSE_SHEET_NAME       = 'フォームの回答 1';
var KARIOKI_SHEET_NAME        = '仮置き';
var SHIFT_REQUEST_SHEET_NAME  = 'シフト希望';
var LOG_SHEET_NAME            = 'GAS_LOG';

var NAME_TITLE                = '氏名';
var GRID_TITLE                = '希望シフト入力';
var WORK_COUNT_TITLE          = '希望勤務回数';
var NOTE_TITLE                = '備考';
var GRID_CAPTION              = '各日のご希望シフトを選択してください';
var SHIFT_COLUMNS             = ['日勤', '夜勤', '段勤', '希望休', '有給'];
var EXTRA_HEADERS             = [WORK_COUNT_TITLE, NOTE_TITLE];

// ==================== メイン ====================

function updateFormAndResetLists() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) {
    throw new Error('別の実行が進行中です。しばらく後にもう一度お試しください。');
  }

  try {
    var form = FormApp.openById(FORM_ID);
    var ss   = SpreadsheetApp.openById(RESPONSE_SPREADSHEET_ID);

    // カレンダーを外部スプシから解凍用スプシへ同期
    syncCalendar_();
    var dates = getCalendarDates_();
    if (dates.length === 0) throw new Error('カレンダーから日付を取得できませんでした');

    var activeSs = SpreadsheetApp.getActiveSpreadsheet();
    activeSs.toast('しばらくお待ちください...', 'フォームリセット中', -1);

    log_('=== 月次更新 開始 ===');
    log_('日付: ' + dates.length + '日 / ' + dates[0] + '〜' + dates[dates.length - 1]);

    // --------------------------------------------------
    // STEP 1: フォームリンクを完全解除
    // --------------------------------------------------
    log_('[STEP 1] removeDestination');
    try {
      form.removeDestination();
      log_('  removeDestination: 成功');
    } catch (e) {
      log_('  removeDestination: ' + e.message + '（無視して継続）');
    }
    Utilities.sleep(3000);

    // --------------------------------------------------
    // STEP 2: 全回答削除
    // --------------------------------------------------
    log_('[STEP 2] deleteAllResponses');
    form.deleteAllResponses();
    Utilities.sleep(1000);

    // --------------------------------------------------
    // STEP 3: フォームアイテム完全削除 → 再生成
    //         ★ リンク解除中に実行するためシートへの書き込みは発生しない
    // --------------------------------------------------
    log_('[STEP 3] rebuildFormItems');
    rebuildFormItems_(form, dates);
    log_('  フォームアイテム再生成: 完了');
    Utilities.sleep(2000);

    // --------------------------------------------------
    // STEP 4: 既存「フォームの回答*」シートを全て一時名にリネーム
    //         setDestination が必ず「新規シート」を作成するよう強制する
    //         （Forms は過去のシートIDを覚えているため名前だけでは不十分）
    // --------------------------------------------------
    log_('[STEP 4] 旧回答シートを一時名にリネーム');
    var oldSheets = [];
    ss.getSheets().forEach(function(s) {
      if (/^フォームの回答/.test(s.getName())) {
        var tempName = '__OLD__' + s.getSheetId();
        s.setName(tempName);
        oldSheets.push(s);
        log_('  リネーム: ' + tempName);
      }
    });
    SpreadsheetApp.flush();
    Utilities.sleep(2000);

    // --------------------------------------------------
    // STEP 5: 現時点のシートID一覧を記録（新シートを後で特定するため）
    // --------------------------------------------------
    var existingIds = ss.getSheets().map(function(s) { return s.getSheetId(); });
    log_('[STEP 5] 既存シートID数: ' + existingIds.length);

    // --------------------------------------------------
    // STEP 6: フォームとスプシを再リンク → Forms が新シートを自動作成
    // --------------------------------------------------
    log_('[STEP 6] setDestination');
    form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());
    SpreadsheetApp.flush();
    log_('  setDestination 完了。新シート生成を待機...');
    Utilities.sleep(8000);

    // --------------------------------------------------
    // STEP 7: 新シートを ID 比較で特定
    // --------------------------------------------------
    log_('[STEP 7] 新シート特定');
    var newSheet = null;
    ss.getSheets().forEach(function(s) {
      if (existingIds.indexOf(s.getSheetId()) < 0) {
        newSheet = s;
        log_('  新シート: 名前=' + s.getName() + '  ID=' + s.getSheetId());
      }
    });

    if (!newSheet) {
      // フォールバック: "__OLD__" 以外で "フォームの回答" で始まるシートを探す
      ss.getSheets().forEach(function(s) {
        if (/^フォームの回答/.test(s.getName())) {
          newSheet = s;
          log_('  フォールバック新シート: ' + s.getName());
        }
      });
    }

    if (!newSheet) {
      throw new Error('[STEP 7] 新シートが見つかりません。Formsとの連携に失敗した可能性があります。');
    }

    // --------------------------------------------------
    // STEP 8: ヘッダー検証（ログのみ。ヘッダーは絶対に変更しない）
    //
    // ★★★ 重要 ★★★
    // リンク済みシートの 1行目を clearContents / setValues すると
    // Forms がヘッダーが消えたと判断し、新しい月の列を右端に追記する。
    // これが過去の「66列になる」問題の原因。
    // setDestination が作成した新シートのヘッダーは Forms が正しく書くため
    // スクリプト側では変更しない。
    // --------------------------------------------------
    log_('[STEP 8] ヘッダー確認（修正なし）');
    var expectedHeaders = getExpectedResponseHeaders_(dates);
    var actualCols      = newSheet.getLastColumn();
    log_('  期待列数: ' + expectedHeaders.length + ' / 実際列数: ' + actualCols);

    if (actualCols > 0) {
      var h = newSheet.getRange(1, 1, 1, actualCols).getDisplayValues()[0];
      log_('  先頭3列: ' + h.slice(0, 3).join(' | '));
      log_('  末尾3列: ' + h.slice(-3).join(' | '));
      if (actualCols === expectedHeaders.length) {
        log_('  列数: 一致 ✓');
      } else {
        log_('  列数: 不一致! ← 後続のリセット処理には影響しないが要確認');
      }
    }

    // --------------------------------------------------
    // STEP 9: データ行（2行目以降）のみクリア
    //         1行目（ヘッダー）は絶対に触れない
    // --------------------------------------------------
    log_('[STEP 9] データ行クリア');
    var lastRow = newSheet.getLastRow();
    if (lastRow > 1) {
      newSheet.getRange(2, 1, lastRow - 1, actualCols).clearContent();
      log_('  ' + (lastRow - 1) + '行クリア');
    } else {
      log_('  データ行なし（スキップ）');
    }

    // --------------------------------------------------
    // STEP 10: 旧シート削除
    // --------------------------------------------------
    log_('[STEP 10] 旧シート削除');
    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMddHHmmss');
    oldSheets.forEach(function(s) {
      try {
        ss.deleteSheet(s);
        log_('  削除: OK');
      } catch (e) {
        try {
          s.setName('旧回答_' + stamp);
          log_('  削除失敗 → リネーム: 旧回答_' + stamp);
        } catch (e2) {
          log_('  削除・リネーム共に失敗: ' + e.message);
        }
      }
    });
    SpreadsheetApp.flush();

    // --------------------------------------------------
    // STEP 11: 新シートを正式名称「フォームの回答 1」に変更
    // --------------------------------------------------
    log_('[STEP 11] 正式名称にリネーム');
    if (newSheet.getName() !== RESPONSE_SHEET_NAME) {
      newSheet.setName(RESPONSE_SHEET_NAME);
      log_('  → ' + RESPONSE_SHEET_NAME);
    } else {
      log_('  既に正式名称');
    }

    // --------------------------------------------------
    // STEP 12: 仮置き・シフト希望シートをリセット
    // --------------------------------------------------
    log_('[STEP 12] 仮置き・シフト希望リセット');
    var karioki  = ss.getSheetByName(KARIOKI_SHEET_NAME);
    var shiftReq = ss.getSheetByName(SHIFT_REQUEST_SHEET_NAME);

    if (karioki)  { resetRequestSheet_(karioki,  dates); log_('  仮置き: OK'); }
    else          { log_('  警告: 仮置きシートが見つかりません'); }

    if (shiftReq) { resetRequestSheet_(shiftReq, dates); log_('  シフト希望: OK'); }
    else          { log_('  警告: シフト希望シートが見つかりません'); }

    log_('=== 月次更新 完了 ===');
    activeSs.toast(dates[0] + '〜' + dates[dates.length - 1] + '（' + dates.length + '日分）', 'フォームリセット完了', 10);

  } catch (e) {
    log_('★エラー発生★: ' + e.message);
    activeSs.toast(e.message, 'エラーが発生しました', 15);
    throw e;

  } finally {
    lock.releaseLock();
  }
}

// ==================== フォームアイテム完全再生成 ====================

function rebuildFormItems_(form, dates) {
  // 既存アイテムを全削除（逆順で削除しないとインデックスがずれる）
  var items = form.getItems();
  for (var i = items.length - 1; i >= 0; i--) {
    form.deleteItem(items[i]);
  }

  // 氏名（職員名簿から候補取得できた場合はリスト式、できなければテキスト式）
  var names = getStaffNames_();
  if (names.length > 0) {
    form.addListItem()
      .setTitle(NAME_TITLE)
      .setChoiceValues(names)
      .setRequired(true);
  } else {
    form.addTextItem()
      .setTitle(NAME_TITLE)
      .setRequired(true);
  }

  // 希望シフト入力（グリッド）
  form.addGridItem()
    .setTitle(GRID_TITLE)
    .setHelpText(GRID_CAPTION)
    .setRows(dates)
    .setColumns(SHIFT_COLUMNS)
    .setRequired(false);

  // 希望勤務回数
  form.addTextItem()
    .setTitle(WORK_COUNT_TITLE)
    .setRequired(false);

  // 備考
  form.addParagraphTextItem()
    .setTitle(NOTE_TITLE)
    .setRequired(false);
}

// ==================== 期待ヘッダー生成 ====================

function getExpectedResponseHeaders_(dates) {
  var headers = ['タイムスタンプ', NAME_TITLE];
  dates.forEach(function(d) {
    headers.push(GRID_TITLE + ' [' + d + ']');
  });
  EXTRA_HEADERS.forEach(function(h) { headers.push(h); });
  return headers;
  // 例: ['タイムスタンプ','氏名','希望シフト入力 [08/01土]',...,'希望勤務回数','備考']
}

// ==================== 仮置き・シフト希望シートリセット ====================

function resetRequestSheet_(sheet, dates) {
  if (!sheet) return;

  var staffNames   = getStaffNames_();
  var holidayData  = getHolidayData_();
  var numDateCols  = dates.length;
  var totalCols    = 1 + numDateCols + EXTRA_HEADERS.length; // 氏名 + 日付× + 備考等

  sheet.clearContents();

  // 1行目: 連番（氏名列は空、日付列は 1〜n、備考等は空）
  var row1 = [''];
  for (var i = 0; i < numDateCols; i++) { row1.push(i + 1); }
  EXTRA_HEADERS.forEach(function() { row1.push(''); });
  sheet.getRange(1, 1, 1, totalCols).setValues([row1]);

  // 2行目: 祝日名（対応する日付に祝日名があれば表示）
  var row2 = [''];
  dates.forEach(function(d) { row2.push(holidayData[d] || ''); });
  EXTRA_HEADERS.forEach(function() { row2.push(''); });
  sheet.getRange(2, 1, 1, totalCols).setValues([row2]);

  // 3行目: ヘッダー（氏名 | 日付1 | 日付2 | ... | 希望勤務回数 | 備考）
  var row3 = ['氏名'];
  dates.forEach(function(d) { row3.push(d); });
  EXTRA_HEADERS.forEach(function(h) { row3.push(h); });
  sheet.getRange(3, 1, 1, totalCols).setValues([row3]);

  // 4行目以降: 職員名を A 列に設定（残りは空白）
  if (staffNames.length > 0) {
    var dataRows = staffNames.map(function(name) {
      var row = [name];
      for (var j = 0; j < numDateCols + EXTRA_HEADERS.length; j++) { row.push(''); }
      return row;
    });
    sheet.getRange(4, 1, staffNames.length, totalCols).setValues(dataRows);
  }

  // 列幅調整
  sheet.setColumnWidth(1, 80);
  for (var col = 2; col <= 1 + numDateCols; col++) {
    sheet.setColumnWidth(col, 45);
  }
  for (var col2 = 2 + numDateCols; col2 <= totalCols; col2++) {
    sheet.setColumnWidth(col2, 90);
  }
}

// ==================== カレンダー同期（外部スプシ → 解凍用スプシ） ====================

function syncCalendar_() {
  try {
    var extSheet = SpreadsheetApp.openById(CALENDAR_SPREADSHEET_ID)
                                 .getSheetByName(CALENDAR_SHEET_NAME);
    if (!extSheet) {
      log_('syncCalendar_: 外部スプシのシート「' + CALENDAR_SHEET_NAME + '」が見つかりません');
      return;
    }
    var data = extSheet.getDataRange().getValues();

    var localSs    = SpreadsheetApp.getActiveSpreadsheet();
    var localSheet = localSs.getSheetByName(LOCAL_CALENDAR_SHEET_NAME)
                   || localSs.insertSheet(LOCAL_CALENDAR_SHEET_NAME);
    localSheet.clearContents();
    if (data.length > 0) {
      localSheet.getRange(1, 1, data.length, data[0].length).setValues(data);
    }
    log_('syncCalendar_: 完了');
  } catch (e) {
    log_('syncCalendar_エラー（外部スプシ読み込み失敗）: ' + e.message);
  }
}

// ==================== 日付取得（カレンダーの D 列） ====================

function getCalendarDates_() {
  var ss    = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(LOCAL_CALENDAR_SHEET_NAME);

  // ローカルカレンダーがなければ外部スプシから直接取得
  if (!sheet) {
    try {
      sheet = SpreadsheetApp.openById(CALENDAR_SPREADSHEET_ID)
                            .getSheetByName(CALENDAR_SHEET_NAME);
    } catch (e) {
      throw new Error('カレンダーシートにアクセスできません: ' + e.message);
    }
  }

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];

  return sheet.getRange(2, 4, lastRow - 1, 1)
    .getDisplayValues()
    .map(function(row) { return String(row[0]).trim(); })
    .filter(function(v) { return v !== ''; });
}

// ==================== 祝日データ取得（カレンダーの E 列） ====================

function getHolidayData_() {
  var holidays = {};
  var ss       = SpreadsheetApp.getActiveSpreadsheet();
  var sheet    = ss.getSheetByName(LOCAL_CALENDAR_SHEET_NAME);
  if (!sheet) return holidays;

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return holidays;

  // D列: 日付表示文字列、E列: 祝日名
  var data = sheet.getRange(2, 4, lastRow - 1, 2).getDisplayValues();
  data.forEach(function(row) {
    var date    = String(row[0]).trim();
    var holiday = String(row[1]).trim();
    if (date && holiday) { holidays[date] = holiday; }
  });
  return holidays;
}

// ==================== 職員名取得（職員名簿の A 列） ====================

function getStaffNames_() {
  try {
    var ss    = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName(STAFF_SHEET_NAME);
    if (!sheet) return [];

    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return [];

    return sheet.getRange(2, 1, lastRow - 1, 1)
      .getDisplayValues()
      .map(function(row) { return String(row[0]).trim(); })
      .filter(function(name) { return name !== ''; });
  } catch (e) {
    log_('getStaffNames_エラー: ' + e.message);
    return [];
  }
}

// ==================== ログ ====================

function log_(message) {
  var ss       = SpreadsheetApp.getActiveSpreadsheet();
  var logSheet = ss.getSheetByName(LOG_SHEET_NAME);
  if (!logSheet) {
    logSheet = ss.insertSheet(LOG_SHEET_NAME);
    logSheet.appendRow(['タイムスタンプ', 'メッセージ']);
  }
  var ts = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'MM/dd HH:mm:ss');
  logSheet.appendRow([ts, message]);
  Logger.log(message);
}
