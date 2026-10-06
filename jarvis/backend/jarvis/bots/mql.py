"""MQL5 source handling: reading, input parameters, the deal export, edits.

JARVIS never changes the user's own files. It works on copies:

- **Export hook**: every copy JARVIS backtests gets an ``OnTester()`` handler
  appended that writes all deals of the run to a CSV file in MetaTrader's
  common Files folder. An ``OnTester`` the EA already has keeps working — it
  is renamed and called first. So results come from the deals themselves,
  not from parsing MetaTrader's report.
- **Edits**: improvements are exact search/replace edits on a version's
  source; an edit whose text isn't found exactly once is refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EXPORT_MARKER = "// --- added by JARVIS: deal export"


class EditError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    text: str  # with "\n" line endings
    encoding: str  # how to write it back
    newline: str  # "\r\n" or "\n"


def read_source(path: Path) -> Source:
    """MetaEditor saves UTF-16 (with BOM) or UTF-8; older files ANSI."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        try:
            raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "cp1252"
    text = raw.decode(encoding)
    newline = "\r\n" if "\r\n" in text else "\n"
    return Source(text.replace("\r\n", "\n"), encoding, newline)


def write_source(path: Path, source: Source) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = source.text.replace("\n", source.newline).encode(source.encoding)
    path.write_bytes(data)


# Inputs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BotInput:
    name: str
    type: str
    default: str
    comment: str
    optimizable: bool  # ``sinput`` parameters are fixed

    @property
    def numeric(self) -> bool:
        return self.type in ("int", "uint", "long", "ulong", "short", "ushort", "char",
                             "uchar", "double", "float")  # fmt: skip


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_INPUT = re.compile(
    r"^\s*(?P<kind>sinput|input)\s+(?!group\b)(?P<type>[A-Za-z_][\w:]*)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:=\s*(?P<default>[^;]*?))?\s*;\s*(?://\s*(?P<comment>.*))?$"
)


def parse_inputs(text: str) -> list[BotInput]:
    """The EA's ``input`` / ``sinput`` parameters, in order."""
    out = []
    for line in _BLOCK_COMMENT.sub("", text).split("\n"):
        match = _INPUT.match(line)
        if match:
            out.append(
                BotInput(
                    name=match["name"],
                    type=match["type"],
                    default=(match["default"] or "").strip(),
                    comment=(match["comment"] or "").strip(),
                    optimizable=match["kind"] == "input",
                )
            )
    return out


# The export hook ---------------------------------------------------------------------

_ON_TESTER = re.compile(r"\bOnTester\b")

_EXPORT = """
{marker} (not part of the original EA)
double OnTester()
  {{
   double jarvis_result = {call};
   if(HistorySelect(0, TimeCurrent()))
     {{
      int handle = FileOpen("{file}", FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
      if(handle != INVALID_HANDLE)
        {{
         FileWrite(handle, "time", "type", "entry", "volume", "price", "profit", "commission",
                   "swap", "symbol", "position");
         int total = HistoryDealsTotal();
         for(int i = 0; i < total; i++)
           {{
            ulong ticket = HistoryDealGetTicket(i);
            if(ticket == 0)
               continue;
            FileWrite(handle,
                      (long)HistoryDealGetInteger(ticket, DEAL_TIME),
                      (long)HistoryDealGetInteger(ticket, DEAL_TYPE),
                      (long)HistoryDealGetInteger(ticket, DEAL_ENTRY),
                      DoubleToString(HistoryDealGetDouble(ticket, DEAL_VOLUME), 2),
                      DoubleToString(HistoryDealGetDouble(ticket, DEAL_PRICE), 5),
                      DoubleToString(HistoryDealGetDouble(ticket, DEAL_PROFIT), 2),
                      DoubleToString(HistoryDealGetDouble(ticket, DEAL_COMMISSION), 2),
                      DoubleToString(HistoryDealGetDouble(ticket, DEAL_SWAP), 2),
                      HistoryDealGetString(ticket, DEAL_SYMBOL),
                      (long)HistoryDealGetInteger(ticket, DEAL_POSITION_ID));
           }}
         FileClose(handle);
        }}
     }}
   return jarvis_result;
  }}
"""


def instrument(text: str, export_file: str) -> str:
    """The source plus the deal export (written to ``export_file`` in Common\\Files)."""
    if EXPORT_MARKER in text:
        text = text[: text.index(EXPORT_MARKER)].rstrip("\n") + "\n"
        text = text.replace("Jarvis_UserOnTester", "OnTester")
    has_own = bool(_ON_TESTER.search(_BLOCK_COMMENT.sub("", _strip_line_comments(text))))
    if has_own:
        text = _ON_TESTER.sub("Jarvis_UserOnTester", text)
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", export_file):
        raise ValueError(f"unsafe export file name {export_file!r}")
    block = _EXPORT.format(
        marker=EXPORT_MARKER,
        call="Jarvis_UserOnTester()" if has_own else "0.0",
        file=export_file,
    )
    return text.rstrip("\n") + "\n" + block


def _strip_line_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


# Edits ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    find: str
    replace: str


def apply_edits(text: str, edits: list[Edit]) -> str:
    """Apply exact search/replace edits in order. Each ``find`` must occur
    exactly once in the text as it is at that point."""
    if not edits:
        raise EditError("no edits")
    for k, edit in enumerate(edits, start=1):
        find = edit.find.replace("\r\n", "\n")
        if not find.strip():
            raise EditError(f"edit {k}: empty search text")
        count = text.count(find)
        if count == 0:
            raise EditError(
                f"edit {k}: the search text isn't in the source (it must match exactly)"
            )
        if count > 1:
            raise EditError(f"edit {k}: the search text occurs {count} times — make it unique")
        text = text.replace(find, edit.replace.replace("\r\n", "\n"), 1)
    return text


def diff_summary(before: str, after: str) -> dict[str, int]:
    """Lines added and removed (for the version list)."""
    import difflib

    added = removed = 0
    for line in difflib.unified_diff(before.split("\n"), after.split("\n"), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"added": added, "removed": removed}
