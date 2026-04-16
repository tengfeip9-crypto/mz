from __future__ import annotations

import csv
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

MODULE_DIR_PATH = Path(__file__).resolve().parent
PROJECT_ROOT_PATH = MODULE_DIR_PATH.parent
if str(PROJECT_ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_PATH))

from project_config import (  # noqa: E402
    CHROMEDRIVER_PATH as DEFAULT_CHROMEDRIVER_PATH,
    DEBUGGER_ADDRESS,
    FRIEND_DATA_DIR as DEFAULT_FRIEND_DATA_DIR,
    QQ_NUMBER,
)

MODULE_DIR = os.fspath(MODULE_DIR_PATH)
PROJECT_ROOT = os.fspath(PROJECT_ROOT_PATH)
FRIEND_DATA_DIR = os.fspath(DEFAULT_FRIEND_DATA_DIR)
SNAPSHOT_DIR = os.path.join(FRIEND_DATA_DIR, "snapshots")
LOG_DIR = os.path.join(FRIEND_DATA_DIR, "logs")
EXPORT_DIR = os.path.join(FRIEND_DATA_DIR, "exports")
STATE_DIR = os.path.join(FRIEND_DATA_DIR, "state")

LATEST_SNAPSHOT_PATH = os.path.join(FRIEND_DATA_DIR, "latest_snapshot.json")
COMPARE_STATE_PATH = os.path.join(STATE_DIR, "compare_state.json")

CHROME_DRIVER_PATH = os.fspath(DEFAULT_CHROMEDRIVER_PATH)


def ensure_friend_dirs() -> None:
    for path in (FRIEND_DATA_DIR, SNAPSHOT_DIR, LOG_DIR, EXPORT_DIR, STATE_DIR):
        os.makedirs(path, exist_ok=True)


def now_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(data: Any, path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def parse_callback_json(text: str) -> dict[str, Any]:
    text = text.strip()
    match = re.search(r"^[^(]+\((.*)\)\s*;?\s*$", text, re.S)
    if match is not None:
        return json.loads(match.group(1))
    return json.loads(text)


def build_friend_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "uin": str(item.get("uin", "")).strip(),
        "name": (item.get("name") or item.get("nickname") or "").strip(),
        "remark": (item.get("remark") or "").strip(),
        "group_id": item.get("groupid"),
    }


def sort_friend_records(friends: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(friends, key=lambda item: item.get("uin", ""))


def make_snapshot(friends: list[dict[str, Any]], qq_number: str | None = None) -> dict[str, Any]:
    sorted_friends = sort_friend_records(friends)
    return {
        "version": 1,
        "qq_number": str((qq_number or QQ_NUMBER or "")).strip(),
        "created_at": now_iso(),
        "friend_count": len(sorted_friends),
        "friends": sorted_friends,
    }


def snapshot_path_from_timestamp(timestamp: str) -> str:
    return os.path.join(SNAPSHOT_DIR, f"好友列表快照_{timestamp}.json")


def write_snapshot(snapshot: dict[str, Any], timestamp: str) -> str:
    ensure_friend_dirs()
    path = snapshot_path_from_timestamp(timestamp)
    save_json(snapshot, path)
    save_json(snapshot, LATEST_SNAPSHOT_PATH)
    return path


def list_snapshot_paths(skip_empty: bool = False) -> list[str]:
    ensure_friend_dirs()
    files = [
        os.path.join(SNAPSHOT_DIR, name)
        for name in os.listdir(SNAPSHOT_DIR)
        if name.lower().endswith(".json")
    ]
    snapshot_paths = sorted(files)
    if not skip_empty:
        return snapshot_paths

    valid_paths: list[str] = []
    for path in snapshot_paths:
        try:
            if snapshot_has_friends(load_json(path)):
                valid_paths.append(path)
        except Exception:
            continue

    return valid_paths


def normalize_snapshot(snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        return {"version": 1, "qq_number": QQ_NUMBER or "", "created_at": None, "friend_count": 0, "friends": []}

    if isinstance(snapshot, list):
        if all(isinstance(item, str) for item in snapshot):
            friends = [{"uin": str(item), "name": "", "remark": "", "group_id": None} for item in snapshot]
            return make_snapshot(friends)

        if all(isinstance(item, dict) for item in snapshot):
            friends = [build_friend_record(item) for item in snapshot]
            return make_snapshot(friends)

    if isinstance(snapshot, dict):
        if "friends" in snapshot:
            friends_data = snapshot.get("friends") or []

            if isinstance(friends_data, dict):
                friends = [
                    {
                        "uin": str(uin),
                        "name": "",
                        "remark": str(remark or ""),
                        "group_id": None,
                    }
                    for uin, remark in friends_data.items()
                ]
            else:
                friends = [build_friend_record(item) for item in friends_data]

            normalized = {
                "version": snapshot.get("version", 1),
                "qq_number": str(snapshot.get("qq_number") or QQ_NUMBER or ""),
                "created_at": snapshot.get("created_at"),
                "friend_count": len(friends),
                "friends": sort_friend_records(friends),
            }
            return normalized

        if all(not isinstance(value, (list, dict)) for value in snapshot.values()):
            friends = [
                {
                    "uin": str(uin),
                    "name": "",
                    "remark": str(remark or ""),
                    "group_id": None,
                }
                for uin, remark in snapshot.items()
            ]
            return make_snapshot(friends)

    raise ValueError("不支持的好友快照格式")


def snapshot_friend_count(snapshot: Any) -> int:
    normalized = normalize_snapshot(snapshot)
    return int(normalized.get("friend_count") or len(normalized.get("friends", [])))


def snapshot_has_friends(snapshot: Any) -> bool:
    return snapshot_friend_count(snapshot) > 0


def snapshot_to_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = normalize_snapshot(snapshot)
    result: dict[str, dict[str, Any]] = {}
    for item in normalized.get("friends", []):
        uin = str(item.get("uin") or "").strip()
        if not uin:
            continue
        result[uin] = {
            "uin": uin,
            "name": (item.get("name") or "").strip(),
            "remark": (item.get("remark") or "").strip(),
            "group_id": item.get("group_id"),
        }
    return result


def friend_display_name(friend: dict[str, Any]) -> str:
    remark = (friend.get("remark") or "").strip()
    name = (friend.get("name") or "").strip()

    if remark and name:
        return f"{remark} ({name})"
    if remark:
        return remark
    if name:
        return name
    return "(无备注/昵称)"


def export_friends_csv(friends: list[dict[str, Any]], timestamp: str) -> str:
    ensure_friend_dirs()
    path = os.path.join(EXPORT_DIR, f"好友列表_{timestamp}.csv")

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["QQ号", "昵称", "备注名", "分组ID"],
        )
        writer.writeheader()

        for item in sort_friend_records(friends):
            writer.writerow(
                {
                    "QQ号": item.get("uin", ""),
                    "昵称": item.get("name", ""),
                    "备注名": item.get("remark", ""),
                    "分组ID": item.get("group_id", ""),
                }
            )

    return path


def excel_column_name(index: int) -> str:
    result = ""
    current = index

    while current >= 0:
        current, remainder = divmod(current, 26)
        result = chr(65 + remainder) + result
        current -= 1

    return result


def make_inline_cell(cell_ref: str, value: Any) -> str:
    text = "" if value is None else str(value)
    escaped = escape(text)
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def make_sheet_xml(rows: list[list[Any]]) -> str:
    xml_rows: list[str] = []

    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            cell_ref = f"{excel_column_name(column_index)}{row_index}"
            cells.append(make_inline_cell(cell_ref, value))
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f"{''.join(xml_rows)}"
        "</sheetData>"
        "</worksheet>"
    )


def export_friends_xlsx(friends: list[dict[str, Any]], timestamp: str) -> str:
    ensure_friend_dirs()
    path = os.path.join(EXPORT_DIR, f"好友列表_{timestamp}.xlsx")
    rows: list[list[Any]] = [["QQ号", "昵称", "备注名", "分组ID"]]

    for item in sort_friend_records(friends):
        rows.append(
            [
                item.get("uin", ""),
                item.get("name", ""),
                item.get("remark", ""),
                item.get("group_id", ""),
            ]
        )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now_iso()}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now_iso()}</dcterms:modified>
</cp:coreProperties>""",
        )
        zf.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="好友列表" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>""",
        )
        zf.writestr("xl/worksheets/sheet1.xml", make_sheet_xml(rows))

    return path
