"""Excel 365 cell checkboxes for openpyxl workbooks.

openpyxl cannot write Excel's in-cell checkboxes (Insert > Checkbox). Excel stores
one as a boolean cell whose cell format carries a "feature property bag"
extension. So callers write boolean cells with ``CHECKBOX_NUMBER_FORMAT`` (a
marker), save, and pass the bytes through :func:`apply_cell_checkboxes`, which
turns every cell format using the marker into a checkbox format and adds the
feature property bag part. Markup matches what XlsxWriter's ``insert_checkbox``
produces.

Excel versions without cell checkboxes (and Google Sheets) show TRUE / FALSE.
"""

from __future__ import annotations

import io
import re
import zipfile

CHECKBOX_NUMBER_FORMAT = '"gg-checkbox"'

_MARKER_XML = "&quot;gg-checkbox&quot;"
_FPB_PATH = "xl/featurePropertyBag/featurePropertyBag.xml"
_FPB_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<FeaturePropertyBags xmlns="http://schemas.microsoft.com/office/'
    'spreadsheetml/2022/featurepropertybag">'
    '<bag type="Checkbox"/>'
    '<bag type="XFControls"><bagId k="CellControl">0</bagId></bag>'
    '<bag type="XFComplement"><bagId k="XFControls">1</bagId></bag>'
    '<bag type="XFComplements" extRef="XFComplementsMapperExtRef">'
    '<a k="MappedFeaturePropertyBags"><bagId>2</bagId></a></bag>'
    "</FeaturePropertyBags>"
)
_FPB_CONTENT_TYPE = (
    '<Override PartName="/xl/featurePropertyBag/featurePropertyBag.xml" '
    'ContentType="application/vnd.ms-excel.featurepropertybag+xml"/>'
)
_FPB_REL_TYPE = (
    "http://schemas.microsoft.com/office/2022/11/relationships/FeaturePropertyBag"
)
_XF_CHECKBOX_EXT = (
    '<extLst><ext uri="{C7286773-470A-42A8-94C5-96B5CB345126}" '
    'xmlns:xfpb="http://schemas.microsoft.com/office/spreadsheetml/2022/'
    'featurepropertybag"><xfpb:xfComplement i="0"/></ext></extLst>'
)


def _marker_numfmt_id(styles: str) -> str | None:
    for m in re.finditer(r"<numFmt\b[^>]*/>", styles):
        tag = m.group(0)
        if f'formatCode="{_MARKER_XML}"' in tag:
            found = re.search(r'numFmtId="(\d+)"', tag)
            return found.group(1) if found else None
    return None


def _mark_checkbox_xfs(styles: str, numfmt_id: str) -> tuple[str, int]:
    """Give every cellXfs ``<xf>`` using the marker format the checkbox extension."""
    start = styles.find("<cellXfs")
    end = styles.find("</cellXfs>", start)
    if start < 0 or end < 0:
        return styles, 0
    block = styles[start:end]
    xf_re = re.compile(r"<xf\b([^>]*?)(/>|>(.*?)</xf>)", re.DOTALL)
    changed = 0

    def repl(m: re.Match) -> str:
        nonlocal changed
        attrs = m.group(1)
        if f'numFmtId="{numfmt_id}"' not in attrs:
            return m.group(0)
        changed += 1
        attrs = attrs.replace(f'numFmtId="{numfmt_id}"', 'numFmtId="0"')
        attrs = re.sub(r'\s+applyNumberFormat="[^"]*"', "", attrs)
        inner = m.group(3) or ""
        return f"<xf{attrs}>{inner}{_XF_CHECKBOX_EXT}</xf>"

    block = xf_re.sub(repl, block)
    return styles[:start] + block + styles[end:], changed


def _add_workbook_rel(rels: str) -> str:
    ids = [int(n) for n in re.findall(r'Id="rId(\d+)"', rels)]
    rid = f"rId{max(ids, default=0) + 1}"
    rel = (
        f'<Relationship Id="{rid}" Type="{_FPB_REL_TYPE}" '
        'Target="featurePropertyBag/featurePropertyBag.xml"/>'
    )
    return rels.replace("</Relationships>", rel + "</Relationships>")


def apply_cell_checkboxes(xlsx: bytes) -> bytes:
    """Turn marker-formatted boolean cells into Excel cell checkboxes."""
    src = zipfile.ZipFile(io.BytesIO(xlsx))
    names = src.namelist()
    if "xl/styles.xml" not in names or _FPB_PATH in names:
        return xlsx
    styles = src.read("xl/styles.xml").decode("utf-8")
    numfmt_id = _marker_numfmt_id(styles)
    if numfmt_id is None:
        return xlsx
    styles, changed = _mark_checkbox_xfs(styles, numfmt_id)
    if not changed:
        return xlsx

    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/styles.xml":
                data = styles.encode("utf-8")
            elif info.filename == "[Content_Types].xml":
                data = (
                    data.decode("utf-8")
                    .replace("</Types>", _FPB_CONTENT_TYPE + "</Types>")
                    .encode("utf-8")
                )
            elif info.filename == "xl/_rels/workbook.xml.rels":
                data = _add_workbook_rel(data.decode("utf-8")).encode("utf-8")
            out.writestr(info, data)
        out.writestr(_FPB_PATH, _FPB_XML)
    return out_buf.getvalue()
