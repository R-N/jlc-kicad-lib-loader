"""Offline checks for everything in the plugin that is pure logic.

    python3 tests/test_offline.py

No network, no KiCad project, no test framework. The renderer section needs
nothing but the standard library; the two sections that import
`component_loader` and `config_manager` need `requests`/`pcbnew` and `wx`
respectively, and are skipped when those are absent instead of failing.

What is deliberately NOT here: anything that needs the EasyEDA API or a real
KiCad importer. Those live in `smoke_download.py` and `smoke_preview.py`, which
must be run by hand because they hit the network.
"""

import json
import logging
import os
import re
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
sys.path.insert(0, ROOT)

# The renderers log a warning on unparseable input, which several checks trigger
# on purpose. Keep the expected noise out of the report.
logging.disable(logging.WARNING)

def loaderModule():
    """`easyeda_lib_loader` imported the way KiCad imports it: as a package.

    Its own imports are relative (`from .component_loader import *`), so a flat
    import cannot work; a symlink gives the repository a package name.
    """
    if "jlcpkg.easyeda_lib_loader" not in sys.modules:
        pkgroot = tempfile.mkdtemp(prefix="jlcpkgroot")
        os.symlink(ROOT, os.path.join(pkgroot, "jlcpkg"))
        sys.path.insert(0, pkgroot)

    from jlcpkg import easyeda_lib_loader

    return easyeda_lib_loader

passed = 0
skipped = []


def check(ok, what):
    global passed
    if not ok:
        raise AssertionError(what)
    passed += 1


def texts(svg):
    return re.findall(r">([^<]*)</text>", svg)


def colors(svg):
    return set(re.findall(r"#[0-9a-fA-F]{6}", svg))


def viewBox(svg):
    return [float(v) for v in re.search(r'viewBox="([^"]+)"', svg).group(1).split()]


def size(svg):
    return (float(re.search(r'width="([\d.]+)"', svg).group(1)),
            float(re.search(r'height="([\d.]+)"', svg).group(1)))


def doc(*lines):
    """A Pro document: one JSON array per line, as the API serves it."""
    return "\n".join(json.dumps(line) for line in lines)


# --------------------------------------------------------------------------
# pro_render: a real captured document
# --------------------------------------------------------------------------

def test_real_document():
    import pro_render

    fixture = json.load(open(os.path.join(FIXTURES, "pro_ams1117.json")))

    # AMS1117-3.3, LCSC C6186: a 4-pin symbol (1 RECT body, 4 PINs, 12 ATTRs of
    # which 8 are visible) and an SOT-223 footprint (4 PADs on copper layer 1,
    # 4 silkscreen POLYs on layer 3, plus POLY/FILL on documentation layers
    # 13/48/49/50/51 that must not be drawn).
    symbol = pro_render.symbolSvg(fixture["symbol"])
    check(symbol.startswith("<svg"), "symbol did not render")
    check(symbol.count("<line") == 4, f"expected 4 pin lines, got {symbol.count('<line')}")
    check(symbol.count("<rect") == 1, f"expected 1 body rect, got {symbol.count('<rect')}")
    check(texts(symbol) == ["In", "3", "Out", "2", "GND", "1", "TAB", "4"],
          f"pin names/numbers wrong: {texts(symbol)}")

    footprint = pro_render.footprintSvg(fixture["footprint"])
    check(footprint.startswith("<svg"), "footprint did not render")
    check(footprint.count("<rect") == 4, f"expected 4 pads, got {footprint.count('<rect')}")
    check(sorted(texts(footprint)) == ["1", "2", "3", "4"],
          f"pad numbers wrong: {texts(footprint)}")

    # The blob regression: layers 48-51 (component shape and marking, pin
    # soldering and floating) are opaque fills that hid the pads.
    docColors = {"#00cccc", "#66ffcc", "#cc9999", "#ff99ff"}
    check(not (colors(footprint) & docColors),
          f"documentation layers drawn: {colors(footprint) & docColors}")
    check("#ff0000" in colors(footprint), "copper layer 1 missing")
    check("#ffcc00" in colors(footprint), "silkscreen layer 3 missing")

    # Pads last, so silkscreen cannot cover them.
    check(footprint.rindex("<rect") > footprint.rindex("#ffcc00"), "silkscreen drawn over pads")

    # Both drawings are capped at the preview pane's size, aspect kept.
    for name, svg in (("symbol", symbol), ("footprint", footprint)):
        w, h = size(svg)
        box = viewBox(svg)
        check(max(w, h) == pro_render.PREVIEW_PX, f"{name} not scaled to preview size: {w}x{h}")
        check(abs(w / h - box[2] / box[3]) < 0.01, f"{name} aspect ratio distorted")


# --------------------------------------------------------------------------
# pro_render: conventions copied from KiCad's importers
# --------------------------------------------------------------------------

def test_y_axis_and_units():
    import pro_render

    # KiCad's parsers negate Y (ScalePos), so a line running up the page must
    # come out with a negative SVG y.
    svg = pro_render.symbolSvg(doc(["POLY", "p1", [0, 0, 0, 100], 0, None]))
    check("L 0.000 -100.000" in svg, f"Y axis not flipped: {svg}")

    # The viewBox is in document units, so a 100-unit tall drawing spans ~100
    # (plus the 4% margin on each side) whatever the pixel size.
    box = viewBox(svg)
    check(103 < box[3] < 112, f"viewBox not in document units: {box}")


def test_pin_geometry():
    import pro_render

    # PIN: [id, ?, x, y, length, rotation, ...]; the stored point is the
    # connection tip and the rotation points back towards the body. A pin at
    # (0,0) rotated 180 therefore ends at (-10, 0).
    svg = pro_render.symbolSvg(doc(["PIN", "p1", None, None, 0, 0, 10, 180, None, 0]))
    check('x2="-10.000"' in svg and 'y2="-0.000"' in svg, f"pin drawn the wrong way: {svg}")

    # A pin marked inverted (index 9 == 2) gets a bubble at the body end.
    inverted = pro_render.symbolSvg(doc(["PIN", "p1", None, None, 0, 0, 10, 180, None, 2]))
    check(inverted.count("<circle") == 1, "inverted pin has no bubble")


def test_font_size():
    import pro_render

    # FONTSTYLE index 5 is the font size; KiCad multiplies it by 0.62 to get the
    # cap height, and the preview must agree or text overflows its shapes.
    svg = pro_render.symbolSvg(doc(["FONTSTYLE", "f1", None, "#123456", None, 10],
                                   ["TEXT", "t1", 0, 0, 0, "hello", "f1"]))
    # Spelled out, not computed from FONT_CAP_RATIO: the constant is the thing
    # under test, and 10 * 0.62 = 6.2 is what KiCad's importer produces.
    check('font-size="6.200"' in svg, f"font size wrong: {svg}")
    check('fill="#123456"' in svg, "FONTSTYLE colour ignored")


def test_pad_shapes_and_holes():
    import pro_render

    layer = ["LAYER", 1, "TOP", None, None, "#ff0000"]
    pads = pro_render.footprintSvg(doc(
        layer,
        ["PAD", "p1", None, None, 1, "1", 0, 0, 0, ["NONE"], ["RECT", 40, 20, 0]],
        ["PAD", "p2", None, None, 1, "2", 100, 0, 0, ["NONE"], ["ELLIPSE", 40, 40]],
        ["PAD", "p3", None, None, 1, "3", 200, 0, 0, ["ROUND", 20, 20], ["ELLIPSE", 40, 40]],
        ["PAD", "p4", None, None, 1, "4", 300, 0, 0, ["NONE"], ["OVAL", 60, 20]],
    ))
    check(pads.count("<rect") == 2, f"RECT and OVAL pads: {pads.count('<rect')} rects")
    check(pads.count("<ellipse") == 2, f"round pads: {pads.count('<ellipse')} ellipses")
    # A drilled pad shows the hole punched through it.
    check('fill="#ffffff"' in pads, "plated hole not drawn")

    # Anything outside ARTWORK_LAYERS is documentation and stays hidden.
    hidden = pro_render.footprintSvg(doc(
        ["LAYER", 48, "COMPONENT_SHAPE", None, None, "#00cccc"],
        ["FILL", "f1", None, None, 48, None, None, [[-100, -100, 100, -100, 100, 100]]],
    ))
    check(hidden == "", f"documentation layer rendered: {hidden}")


def test_contour_primitives():
    import pro_render

    # ParseContour's tokens: straight runs, ARC (radius, ...), and CIRCLE/R as
    # standalone primitives.
    arc = pro_render.footprintSvg(doc(
        ["LAYER", 3, "SILK", None, None, "#ffcc00"],
        ["POLY", "s1", None, None, 3, 1, [0, 0, "ARC", 50, 0, 1, 100, 0, "L", 100, 100]],
    ))
    check("<path" in arc and " A " in arc, f"arc token not rendered as an arc: {arc}")

    ring = pro_render.footprintSvg(doc(
        ["LAYER", 3, "SILK", None, None, "#ffcc00"],
        ["POLY", "s1", None, None, 3, 1, ["CIRCLE", 0, 0, 50]],
    ))
    check("<circle" in ring, f"circle primitive not rendered: {ring}")


def test_empty_and_broken_documents():
    import pro_render

    # Placeholder and title-block entries are common in the public library; they
    # must come back empty so the preview can explain itself, not raise.
    for bad in ("", None, "not json at all", "[unclosed", '["DOCTYPE","2"]',
                doc(["HEAD", {}], ["PART", "p", None])):
        check(pro_render.symbolSvg(bad) == "", f"symbol should be empty for {bad!r}")
        check(pro_render.footprintSvg(bad) == "", f"footprint should be empty for {bad!r}")

    # A document that is partly broken still draws what it can.
    partial = pro_render.symbolSvg("[garbage\n" + json.dumps(["POLY", "p", [0, 0, 10, 10], 0, None]))
    check(partial.startswith("<svg"), "a single bad line threw the whole document away")


# --------------------------------------------------------------------------
# component_loader: the EasyEDA Std library format
# --------------------------------------------------------------------------

SYMBOL_DOC = {
    "title": "Widget",
    "dataStr": {
        "head": {"docType": "2", "x": 0, "y": 0, "c_para": {"name": "WIDGET", "pre": "U?"}},
        "shape": ["P~show~0~1~0~0~0~gge1"],
    },
    "packageDetail": {
        "title": "Widget package", "uuid": "fp-uuid",
        "dataStr": {
            "head": {"docType": "4", "x": 0, "y": 0, "c_para": {"package": "WIDGET-PKG"}},
            "layers": ["1~TopLayer~#FF0000~true~false~true~"],
            "shape": [
                "PAD~ELLIPSE~4000~3000~2~2~1~1~gge2",
                "SVGNODE~" + json.dumps({"attrs": {
                    "c_etype": "outline3D", "uuid": "model-uuid", "title": "WIDGET_3D",
                    "c_width": "39.37", "c_height": "78.74", "z": "0"}}),
            ],
        },
    },
}

FOOTPRINT_DOC = {
    "title": "Lonely footprint",
    "dataStr": {
        "head": {"docType": "4", "x": 0, "y": 0, "c_para": {"package": "LONELY-PKG"}},
        "layers": ["1~TopLayer~#FF0000~true~false~true~"],
        "shape": ["PAD~RECT~4000~3000~2~2~1~1~gge1"],
    },
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    """Answers the Std component endpoint from a uuid -> document mapping."""

    def __init__(self, documents):
        self.documents = documents

    def get(self, url, **kwargs):
        uuid = url.rsplit("/", 1)[-1]
        return FakeResponse({"success": True, "result": self.documents[uuid]})


def test_std_library():
    import component_loader as cl

    check(cl.splitSources(["C123", " std:abc ", "", "def"]) == (["C123", "def"], ["abc"]),
          "part list not split by source")
    check(cl.getUuidFirstPart("uuid|owner") == "uuid", "owner suffix not stripped")

    # `~` and `#@$` are the field separators of the Std shape format, and a value
    # carrying one would split the shape into nonsense.
    check(cl.sanitizeStdValue("a~b#@$c`d") == "a b c'd", "separators not escaped out of values")

    shape = cl.buildStdLibShape(SYMBOL_DOC["dataStr"], {"spiceSymbolName": "WIDGET"}, "gge1")
    check(shape.startswith("LIB~0~0~"), f"shape root malformed: {shape[:40]}")
    check(cl.stdShapeName(shape, "spiceSymbolName") == "WIDGET", "name not readable back")
    check(shape.split("#@$")[1:] == SYMBOL_DOC["dataStr"]["shape"],
          "document shapes were not passed through verbatim")

    kiprjmod = tempfile.mkdtemp(prefix="jlctest")
    target = os.path.join(kiprjmod, "lib")
    loader = cl.ComponentLoader(kiprjmod=kiprjmod, target_path=target, target_name="Test",
                                progress=lambda done, total: None,
                                session=FakeSession({"sym": SYMBOL_DOC, "fp": FOOTPRINT_DOC}))

    # docType 2 carries its footprint in packageDetail; docType 4 *is* a
    # footprint. Getting this wrong filed footprints as symbols.
    models, symbolCount, footprintCount = loader.downloadStd(["sym", "fp"])
    check((symbolCount, footprintCount) == (1, 2),
          f"counts reported to the caller are wrong: {symbolCount}, {footprintCount}")
    zip_path = os.path.join(target, "Test-std.zip")

    with zipfile.ZipFile(zip_path) as zf:
        symbolDoc = json.loads(zf.read("symbols.json"))
        footprintDoc = json.loads(zf.read("footprints.json"))

    symbols = symbolDoc["schematics"][0]["dataStr"]["shape"]
    footprints = footprintDoc["shape"]
    check([cl.stdShapeName(s, "spiceSymbolName") for s in symbols] == ["WIDGET"],
          "symbol document mis-filed")
    check(sorted(cl.stdShapeName(f, "package") for f in footprints)
          == ["LONELY-PKG", "WIDGET-PKG"], "standalone footprint mis-filed")

    # The docTypes KiCad's importers dispatch on, and the layer palette a
    # footprint document is unreadable without.
    check(symbolDoc["docType"] == 5, "symbol document is not a docType 5 schematic list")
    check(footprintDoc["head"]["docType"] == "3", "footprint document is not a docType 3 PCB")
    check(footprintDoc["layers"] == SYMBOL_DOC["packageDetail"]["dataStr"]["layers"],
          "layer palette lost")

    # 3D model tasks: EasyEDA Std sizes are in units of 10 mil, and models always
    # go under $KIPRJMOD/EASYEDA_MODELS whatever the library path.
    path, fitX, fitY = models["model-uuid"]
    check(abs(fitX - 10.0) < 0.01 and abs(fitY - 20.0) < 0.01,
          f"Std 3D size not converted to mm: {fitX}, {fitY}")
    check(path == os.path.join(kiprjmod, cl.MODELS_DIR, "WIDGET_3D.step"), f"model path {path}")

    # Re-downloading one part must keep the rest of the library.
    loader.session = FakeSession({"fp": FOOTPRINT_DOC})
    loader.downloadStd(["fp"])

    with zipfile.ZipFile(zip_path) as zf:
        merged = json.loads(zf.read("footprints.json"))["shape"]
        keptSymbols = json.loads(zf.read("symbols.json"))["schematics"][0]["dataStr"]["shape"]

    check(sorted(cl.stdShapeName(f, "package") for f in merged) == ["LONELY-PKG", "WIDGET-PKG"],
          "merge lost a footprint")
    check(len(keptSymbols) == 1, "merge lost a symbol")

    # Shape ids are NOT unique after a merge - each download numbers from gge1 -
    # and that is fine: KiCad's importer keys footprints on the `package`
    # parameter, and enumerates both entries of a zip holding two `gge1` shapes.

    # A version that could not tell docType 2 from 4 wrapped footprint documents as
    # symbols. Those entries are verbatim from a library that version produced: no
    # `name`, and `spiceSymbolName` equal to the package. Two of them, because the
    # footprint of one was never filed as a footprint at all, so the stale symbol is
    # the only trace of it and matching against footprints.json would miss it.
    misfiled = [
        "LIB~4000~3000~package`ADAFRUIT-MAX17048-GOED`pre`U?`Contributor`liekens.thije`link``"
        "spiceSymbolName`ADAFRUIT-MAX17048-GOED~~~gge1~1#@$RECT~4000~3000~40~20~3~1~gge2~0~",
        "LIB~4000~3000~package`5580_MAX17048_FOOTPRINT`pre`U?`Contributor`furai03`link``"
        "3DModel`5580 MAX17048`spiceSymbolName`5580_MAX17048_FOOTPRINT~~~gge1~1"
        "#@$RECT~4000~3000~40~20~3~1~gge2~0~",
    ]

    # A real symbol whose package is named after it, verbatim from the same library:
    # `package` and `spiceSymbolName` match here too, so only the missing `name`
    # distinguishes a misfiled footprint. This one must survive.
    genuine = ("LIB~360~260~name`CH9340`package`CH9340`pre`U?`Manufacturer``Manufacturer Part``"
               "Supplier``Supplier Part``link``Contributor`skuzmich`spiceSymbolName`CH9340"
               "~~~gge1~1#@$RECT~360~260~40~20~3~1~gge2~0~")

    with zipfile.ZipFile(zip_path) as zf:
        symbolDoc = json.loads(zf.read("symbols.json"))
        footprintDoc = json.loads(zf.read("footprints.json"))

    check(all(cl.isMisfiledFootprint(shape) for shape in misfiled),
          "the misfiled-footprint signature does not match what the old version wrote")
    check(not cl.isMisfiledFootprint(genuine),
          "a symbol sharing its name with its package looks misfiled")
    check(not any(cl.isMisfiledFootprint(shape)
                  for shape in symbolDoc["schematics"][0]["dataStr"]["shape"]),
          "a genuine symbol looks misfiled")

    symbolDoc["schematics"][0]["dataStr"]["shape"].extend(misfiled + [genuine])

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("symbols.json", json.dumps(symbolDoc))
        zf.writestr("footprints.json", json.dumps(footprintDoc))

    loader.session = FakeSession({"fp": FOOTPRINT_DOC})
    loader.downloadStd(["fp"])

    with zipfile.ZipFile(zip_path) as zf:
        keptSymbols = json.loads(zf.read("symbols.json"))["schematics"][0]["dataStr"]["shape"]
        keptFootprints = json.loads(zf.read("footprints.json"))["shape"]

    names = sorted(cl.stdShapeName(s, "spiceSymbolName") for s in keptSymbols)
    check(names == ["CH9340", "WIDGET"], f"wrong symbols kept: {names}")
    check(sorted(cl.stdShapeName(f, "package") for f in keptFootprints)
          == ["LONELY-PKG", "WIDGET-PKG"], "dropping the stale symbols cost a footprint")


# --------------------------------------------------------------------------
# component_loader: the .elibz index must describe what the zip contains
# --------------------------------------------------------------------------

def test_library_index():
    import component_loader as cl

    # Shaped like a real library that lost documents: KiCad enumerates from device.json
    # and loads by name, so an indexed entry with no document appears in the chooser and
    # fails to load, which stops the scan and hides every other footprint in the library.
    lib = {
        "devices": {
            "dev-ok": {"display_title": "AO3401A_C15127",
                       "attributes": {"Symbol": "sym-ok", "Footprint": "fp-ok"}},
            "dev-broken": {"display_title": "J5019 MINI MODULE",
                           "attributes": {"Symbol": "sym-gone", "Footprint": "fp-gone"}},
        },
        "symbols": {"sym-ok": {"display_title": "AO3401A"}, "sym-gone": {"display_title": "J5019"}},
        "footprints": {"fp-ok": {"display_title": "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR"},
                       "fp-gone": {"display_title": "J5019 MINI MODULE"}},
    }

    dropped = cl.pruneOrphans(lib, {"sym-ok": "doc"}, {"fp-ok": "doc"})
    check(dropped == 3, f"expected the orphan symbol, footprint and device to go: {dropped}")
    check(list(lib["footprints"]) == ["fp-ok"], f"orphan footprint kept: {list(lib['footprints'])}")
    check(list(lib["symbols"]) == ["sym-ok"], f"orphan symbol kept: {list(lib['symbols'])}")
    check(list(lib["devices"]) == ["dev-ok"], f"unusable device kept: {list(lib['devices'])}")
    check(cl.pruneOrphans(lib, {"sym-ok": "doc"}, {"fp-ok": "doc"}) == 0,
          "pruning a consistent library must change nothing")

    # Three user-contributed documents all titled J5019: KiCad reaches one name once, so
    # two of them were invisible.
    entries = {"b-uuid": {"display_title": "J5019"}, "a-uuid": {"display_title": "J5019"},
               "c-uuid": {"display_title": "J5019 EDIT"}}
    cl.uniquifyTitles(entries)
    titles = sorted(cl.entryTitle(e) for e in entries.values())
    check(len(set(titles)) == 3, f"titles still collide: {titles}")
    # The uuid that sorts first keeps the plain name, so a board already referencing
    # "J5019" keeps resolving to the same document across downloads.
    check(cl.entryTitle(entries["a-uuid"]) == "J5019", f"kept the wrong one: {titles}")
    check(cl.entryTitle(entries["b-uuid"]) == "J5019 (b-uu)", f"renamed to {titles}")
    check(cl.entryTitle(entries["c-uuid"]) == "J5019 EDIT", "renamed an entry that was unique")


def test_pro_result():
    import component_loader as cl

    check(cl.proResult({"success": True, "result": {"uuid": "x"}}) == {"uuid": "x"},
          "a good response must return its result")

    # EasyEDA Pro signals failure with HTTP 200 and success=false; the message is the
    # only clue, so it must surface instead of a bare KeyError('result').
    try:
        cl.proResult({"success": False, "code": 404, "message": "Component not found"})
        check(False, "a failed response must raise")
    except Exception as e:
        check("Component not found" in str(e) and "404" in str(e),
              f"error hides the API message: {e}")

    try:
        cl.proResult({"success": True})
        check(False, "a response with no result must raise")
    except Exception:
        pass


def test_part_aliases():
    import component_loader as cl

    # A Pro device: the footprint document is named after the package, which is not what
    # anyone types into the footprint chooser.
    lib = {
        "devices": {
            "dev": {"display_title": "AO3401A_C15127", "product_code": "C15127",
                    "attributes": {"Manufacturer Part": "AO3401A",
                                   "Symbol": "sym", "Footprint": "fp",
                                   "3D Model": "model-uuid"}},
        },
        "symbols": {"sym": {"display_title": "AO3401A"}},
        "footprints": {"fp": {"display_title": "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR",
                              "model_3d": {"uri": "model-uuid"}}},
    }
    docs = {"fp": "FOOTPRINT DOCUMENT"}

    check(cl.addPartAliases(lib, docs) == 1, "no alias added for a device with an MPN")

    titles = sorted(cl.entryTitle(e) for e in lib["footprints"].values())
    check(titles == ["AO3401A", "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR"], f"titles are {titles}")

    # The alias uuid keys on the device, so the same part re-downloaded gives the same alias.
    alias_uuid = cl.aliasUuid("dev", "AO3401A")
    check(alias_uuid in lib["footprints"], "alias not keyed by its own uuid")
    check(docs[alias_uuid] == docs["fp"], "alias must carry the same document")
    # The package entry is what placed boards reference; renaming it would break them.
    check(cl.entryTitle(lib["footprints"]["fp"]) == "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR",
          "the package entry must keep its name")
    # The alias is a real entry, so it needs everything the importer reads off it.
    check(lib["footprints"][alias_uuid]["model_3d"]["uri"] == "model-uuid",
          "alias lost the 3D model reference")

    # The importer reads a footprint's 3D model off whichever device points at that uuid,
    # so the alias needs its own device or it places with no body.
    owners = [d for d in lib["devices"].values()
              if d["attributes"]["Footprint"] == alias_uuid]
    check(len(owners) == 1, f"alias has {len(owners)} devices pointing at it")
    check(owners[0]["attributes"]["3D Model"] == "model-uuid", "alias device lost the model")
    check(owners[0].get(cl.ALIAS_MARKER) is True, "alias device not marked")
    check(lib["devices"]["dev"]["attributes"]["Footprint"] == "fp",
          "aliasing repointed the original device")

    # Both entries must survive the prune that runs on every write.
    check(cl.pruneOrphans(lib, {"sym": "doc"}, docs) == 0,
          "pruning dropped the alias or its device")

    # Re-downloading the same part must not grow the library: aliases are rebuilt in place,
    # not merged, so the count stays flat and the uuid is the same.
    check(cl.addPartAliases(lib, docs) == 1, "rebuilt alias missing")
    check(len(lib["footprints"]) == 2, f"library grew: {sorted(lib['footprints'])}")
    check(len(lib["devices"]) == 2, f"devices grew: {sorted(lib['devices'])}")
    check(alias_uuid in lib["footprints"], "rebuild changed the alias uuid")

    # Nothing to alias: no manufacturer part, and a footprint already named after the part.
    bare = {"devices": {"d": {"attributes": {"Footprint": "fp"}}},
            "symbols": {}, "footprints": {"fp": {"display_title": "SOT-23"}}}
    check(cl.addPartAliases(bare, {"fp": "doc"}) == 0, "aliased a device with no part number")

    same = {"devices": {"d": {"attributes": {"Manufacturer Part": "J5019", "Footprint": "fp"}}},
            "symbols": {}, "footprints": {"fp": {"display_title": "J5019"}}}
    check(cl.addPartAliases(same, {"fp": "doc"}) == 0,
          "aliased a footprint that already carries the part name")

    # A device whose document never arrived must not gain an entry pointing at nothing:
    # one unloadable entry is what hides a whole library from the chooser.
    missing = {"devices": {"d": {"attributes": {"Manufacturer Part": "X", "Footprint": "gone"}}},
               "symbols": {}, "footprints": {}}
    check(cl.addPartAliases(missing, {}) == 0, "aliased a footprint with no document")


def test_alias_runaway_cleanup():
    import component_loader as cl

    # The first version of the alias pass keyed the alias on the footprint uuid and
    # re-aliased the alias device itself, so every download added a fresh copy. That
    # left a library like this: one real device, several unmarked copies, and several
    # part-named footprints, some renamed by uniquifyTitles.
    real = {"display_title": "AO3401A_C15127",
            "attributes": {"Manufacturer Part": "AO3401A", "Symbol": "sym", "Footprint": "fp"}}
    lib = {
        "devices": {
            "dev-real": real,
            "dev-copy1": {**{k: v for k, v in real.items()},
                          "attributes": {**real["attributes"], "Footprint": "alias1"}},
            "dev-copy2": {**{k: v for k, v in real.items()},
                          "attributes": {**real["attributes"], "Footprint": "alias2"}},
        },
        "symbols": {"sym": {"display_title": "AO3401A"}},
        "footprints": {
            "fp": {"display_title": "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR"},
            "alias1": {"display_title": "AO3401A"},
            "alias2": {"display_title": "AO3401A (bc11)"},
        },
    }
    docs = {"fp": "DOC", "alias1": "DOC", "alias2": "DOC"}

    cl.addPartAliases(lib, docs)

    # Exactly one alias remains, keyed on the real device, and both stray copies are gone.
    check(len(lib["footprints"]) == 2, f"stray footprints kept: {sorted(lib['footprints'])}")
    check(len(lib["devices"]) == 2, f"stray devices kept: {sorted(lib['devices'])}")
    check(cl.aliasUuid("dev-real", "AO3401A") in lib["footprints"],
          "the surviving alias is not keyed on the real device")
    check(all(e.get(cl.ALIAS_MARKER) for u, e in lib["footprints"].items() if u != "fp"),
          "surviving alias not marked")

    # And a second pass changes nothing.
    cl.addPartAliases(lib, docs)
    check(len(lib["footprints"]) == 2 and len(lib["devices"]) == 2,
          "cleanup not stable across passes")

    # A genuine part-named footprint that a real device references is not an alias and
    # must survive.
    kept = {"devices": {"d": {"display_title": "J5019",
                              "attributes": {"Manufacturer Part": "J5019", "Footprint": "fp"}}},
            "symbols": {}, "footprints": {"fp": {"display_title": "J5019"}}}
    cl.addPartAliases(kept, {"fp": "doc"})
    check(len(kept["footprints"]) == 1 and len(kept["devices"]) == 1,
          "deleted a genuine part-named footprint")


# --------------------------------------------------------------------------
# config_manager: library table rows
# --------------------------------------------------------------------------

def test_library_tables():
    import config_manager as cm

    mgr = cm.LibraryTableManager
    check(mgr.entry_name("Lib", "pro") == "Lib", "Pro entry renamed")
    check(mgr.entry_name("Lib", "std") == "Lib_Std", "Std entry must not collide with Pro")
    check(mgr.entry_uri("Lib", "pro").endswith("Lib.elibz"), "Pro uri is the .elibz")
    check(mgr.entry_uri("Lib", "std").endswith("Lib-std.zip"), "Std uri is the -std.zip")

    # The four plugin type strings KiCad matches on; a typo here silently gives
    # an unreadable library.
    check(mgr.LIB_ENTRY_TYPES[("pro", "symbol")] == "EasyEDA (JLCEDA) Pro", "pro symbol type")
    check(mgr.LIB_ENTRY_TYPES[("pro", "footprint")] == "EasyEDA / JLCEDA Pro", "pro fp type")
    check(mgr.LIB_ENTRY_TYPES[("std", "symbol")] == "EasyEDA (JLCEDA) Std", "std symbol type")
    check(mgr.LIB_ENTRY_TYPES[("std", "footprint")] == "EasyEDA / JLCEDA Std", "std fp type")

    kiprjmod = tempfile.mkdtemp(prefix="jlctest")
    tables = cm.LibraryTableManager(kiprjmod)

    for source in ("pro", "std"):
        for kind in ("symbol", "footprint"):
            check(not tables.check_library_exists(mgr.entry_name("Lib", source), kind),
                  "library reported present before it was added")
            check(tables.add_library_to_table("Lib", "${KIPRJMOD}/EasyEDA_Lib", kind, source),
                  f"failed to add {source} {kind} entry")
            check(tables.check_library_exists(mgr.entry_name("Lib", source), kind),
                  f"{source} {kind} entry not found after adding")

    written = open(os.path.join(kiprjmod, "fp-lib-table")).read()
    check(written.count("(lib ") == 2, f"expected two footprint rows:\n{written}")
    check("EasyEDA / JLCEDA Std" in written and "EasyEDA / JLCEDA Pro" in written,
          "both plugin types must appear in the footprint table")

    # Adding twice must not duplicate the row.
    tables.add_library_to_table("Lib", "${KIPRJMOD}/EasyEDA_Lib", "footprint", "pro")
    again = open(os.path.join(kiprjmod, "fp-lib-table")).read()
    check(again.count("(lib ") == 2, "duplicate library row written")

    # Persisted library name round-trip.
    config = cm.ConfigManager(kiprjmod)
    config.set_library_name("Chosen")
    check(cm.ConfigManager(kiprjmod).get_library_name() == "Chosen", "library name not persisted")

    # A global row that steals the project nickname hides the project library
    # from KiCad entirely, so it must be reported. Global tables are written by
    # KiCad itself and use spaces between the fields, project ones do not.
    global_dir = tempfile.mkdtemp(prefix="jlcglobal")
    tables.global_table_path = lambda kind, _dir=global_dir: os.path.join(
        _dir, "sym-lib-table" if kind == "symbol" else "fp-lib-table")

    check(tables.find_global_conflict("Lib", "pro", "footprint") is None,
          "conflict reported with no global table at all")

    # Lib_Std is written first on purpose: a matcher that treats the nickname as
    # a prefix would return this row for the plain "Lib" query.
    with open(os.path.join(global_dir, "fp-lib-table"), "w") as f:
        f.write('(fp_lib_table\n  (version 7)\n'
                '  (lib (name "Other") (type "KiCad") (uri "/x/Other.pretty") (options "") (descr ""))\n'
                '  (lib (name "Lib_Std") (type "EasyEDA / JLCEDA Std") (uri "/x/Elsewhere-std.zip") (options "") (descr ""))\n'
                '  (lib (name "Lib") (type "EasyEDA / JLCEDA Std") (uri "/x/Lib-std.zip") (options "") (descr ""))\n'
                ')\n')

    conflict = tables.find_global_conflict("Lib", "pro", "footprint")
    check(conflict == "/x/Lib-std.zip", f"shadowing global row not found: {conflict}")
    check(tables.find_global_conflict("Lib", "pro", "symbol") is None,
          "a footprint-table row must not be reported against the symbol table")

    # "Lib" and "Lib_Std" are different libraries: neither nickname may be
    # matched against the other, in either direction.
    std_conflict = tables.find_global_conflict("Lib", "std", "footprint")
    check(std_conflict == "/x/Elsewhere-std.zip",
          f"the Lib_Std row must match the Std nickname, got {std_conflict}")
    check("Lib-std.zip" not in (std_conflict or ""),
          "the 'Lib' row was reported for the 'Lib_Std' nickname")

    # Same nickname pointing at the very file we would write is harmless: that
    # is a user who registered the library globally instead of per project.
    with open(os.path.join(global_dir, "fp-lib-table"), "w") as f:
        f.write('(fp_lib_table\n'
                '  (lib (name "Lib") (type "EasyEDA / JLCEDA Pro") (uri "/other/place/Lib.elibz") (options "") (descr ""))\n'
                ')\n')
    check(tables.find_global_conflict("Lib", "pro", "footprint") is None,
          "a global row pointing at the same file name must not be flagged")


# --------------------------------------------------------------------------
# easyeda_lib_loader: the grid rows and the download queue
# --------------------------------------------------------------------------

PRO_DEVICE = {
    "uuid": "8209ba65d24940569c88b6b832f4ceb3",
    "product_code": "C6186",
    "display_title": "AMS1117-3.3_C6186",
    "footprint": {"display_title": "SOT-223-3_L6.5-W3.4-P2.30-LS7.0-BR"},
    "creator": {"nickname": "LCSC"},
    "attributes": {"LCSC Part Name": "1A low dropout 3.3V regulator",
                   "Manufacturer Part": "AMS1117-3.3", "Supplier Footprint": "SOT-223",
                   "JLCPCB Part Class": "Basic Part", "Supplier Part": "C6186",
                   "Dropout Voltage": "1.1V"},
}

STD_FOOTPRINT_ENTRY = {
    "uuid": "446b46a6b72d4f39850e4a16d55d1a00",
    "title": "MAX17048_BREAKOUT",
    "owner": {"username": "adafruit"},
    "dataStr": {"head": {"docType": "4", "c_para": {"package": "MAX17048_BREAKOUT"}}},
}


def test_result_rows():
    ell = loaderModule()

    row = ell.proRow(PRO_DEVICE, ell.SOURCE_SYSTEM)
    check(len(row) == len(ell.RESULT_COLUMNS) + 1,
          "row is the columns plus one hidden searchable cell")
    check(row[0] == "System", "source not taken from the facet")
    check(row[1] == "C6186", "code column")
    # The Name column is the description, not a second copy of the part number.
    check(row[2] == "1A low dropout 3.3V regulator", f"name column shows {row[2]!r}")
    check(row[3] == "AMS1117-3.3", "MPN column")
    # The document title is unreadable; the supplier package is what a person knows.
    check(row[4] == "SOT-223", f"package column shows {row[4]!r}")
    check(row[5] == "Basic", f"part class should drop the word Part: {row[5]!r}")
    check(row[7] == "LCSC", "contributor column")

    # An uncoded device in JLC's own catalogue is still a JLC System part: the facet
    # decides, not the presence of a code.
    uncoded = dict(PRO_DEVICE, product_code="", attributes={})
    check(ell.proRow(uncoded, ell.SOURCE_SYSTEM)[0] == "System", "source overridden by the code")
    check(ell.proRow(uncoded, ell.SOURCE_SYSTEM)[1] == PRO_DEVICE["uuid"],
          "a codeless device must fall back to its uuid")

    # A Std standalone footprint has to be distinguishable from a symbol.
    footprint = ell.stdRow(STD_FOOTPRINT_ENTRY)
    check(footprint[0] == "Std" and footprint[6] == "Footprint", f"std footprint row: {footprint}")
    check(footprint[1] == "std:" + STD_FOOTPRINT_ENTRY["uuid"], "std rows need the std: prefix")
    check(footprint[7] == "adafruit", "contributor from owner.username")

    symbolEntry = json.loads(json.dumps(STD_FOOTPRINT_ENTRY))
    symbolEntry["dataStr"]["head"]["docType"] = "2"
    check(ell.stdRow(symbolEntry)[6] == "Symbol", "docType 2 is a symbol")


def test_filter_and_sort():
    ell = loaderModule()

    rows = [ell.proRow(PRO_DEVICE, ell.SOURCE_SYSTEM), ell.stdRow(STD_FOOTPRINT_ENTRY)]

    # A parameter value in no grid column must still match via the hidden search cell.
    check(ell.rowMatches(rows[0], "1.1V"), "parameter value not searched")
    # Every term must match somewhere in the row, in any column, either case.
    check(ell.rowMatches(rows[0], ""), "an empty filter must keep everything")
    check(ell.rowMatches(rows[0], "sot-223"), "package not searched")
    check(ell.rowMatches(rows[0], "ams1117 basic"), "terms must combine")
    check(not ell.rowMatches(rows[0], "ams1117 esp32"), "a missing term must exclude the row")
    check(ell.rowMatches(rows[1], "adafruit"), "contributor not searched")
    check(not ell.rowMatches(rows[1], "C6186"), "filter leaked across rows")

    # Codes sort numerically, so C90 comes before C1000 instead of after it.
    codes = ["C1000", "C90", "C6186"]
    ordered = sorted(codes, key=lambda code: ell.sortKey((None, code), 1))
    check(ordered == ["C90", "C1000", "C6186"], f"codes sorted as text: {ordered}")

    names = ["beta", "Alpha", "gamma"]
    ordered = sorted(names, key=lambda name: ell.sortKey((None, None, name), 2))
    check(ordered == ["Alpha", "beta", "gamma"], f"names not case-insensitive: {ordered}")


def test_part_queue():
    ell = loaderModule()

    queue = ell.PartQueue()
    row = ell.proRow(PRO_DEVICE, ell.SOURCE_SYSTEM)

    check(queue.addRows([row]) == 1, "row not queued")
    check(queue.addRows([row]) == 0, "the same part must not queue twice")
    check(len(queue) == 1 and queue.codes() == ["C6186"], f"queue holds {queue.rows()}")

    # Pasted text is the power-user path; the source is inferred from the code shape.
    check(queue.addCodes("C2040\n std:4c0dae4e \n\nC6186\n") == 2,
          "pasted codes: two new, one blank, one duplicate")
    check(queue.codes() == ["C6186", "C2040", "std:4c0dae4e"], f"order lost: {queue.codes()}")
    check(dict(zip(queue.codes(), (row[0] for row in queue.rows())))["std:4c0dae4e"] == "Std",
          "a std: code must be labelled Std")

    queue.remove(["C2040"])
    check(queue.codes() == ["C6186", "std:4c0dae4e"], "remove took the wrong entry")
    queue.clear()
    check(len(queue) == 0 and not queue.codes(), "clear left something behind")

    # What the download path consumes: splitSources must understand every queued code.
    import component_loader as cl
    queue.addCodes("C6186\nstd:4c0dae4e\n8209ba65d24940569c88b6b832f4ceb3\n")
    pro, std = cl.splitSources(queue.codes())
    check(pro == ["C6186", "8209ba65d24940569c88b6b832f4ceb3"] and std == ["4c0dae4e"],
          f"queue does not feed the loader cleanly: {pro}, {std}")


SECTIONS = [
    ("pro_render, real document", test_real_document, ()),
    ("pro_render, axis and units", test_y_axis_and_units, ()),
    ("pro_render, pin geometry", test_pin_geometry, ()),
    ("pro_render, text", test_font_size, ()),
    ("pro_render, pads and layers", test_pad_shapes_and_holes, ()),
    ("pro_render, contours", test_contour_primitives, ()),
    ("pro_render, empty documents", test_empty_and_broken_documents, ()),
    ("component_loader, Std library", test_std_library, ("requests", "pcbnew")),
    ("easyeda_lib_loader, result rows", test_result_rows, ("requests", "pcbnew", "wx")),
    ("easyeda_lib_loader, filter and sort", test_filter_and_sort, ("requests", "pcbnew", "wx")),
    ("easyeda_lib_loader, part queue", test_part_queue, ("requests", "pcbnew", "wx")),
    ("component_loader, library index", test_library_index, ("requests", "pcbnew")),
    ("component_loader, part aliases", test_part_aliases, ("requests", "pcbnew")),
    ("component_loader, alias cleanup", test_alias_runaway_cleanup, ("requests", "pcbnew")),
    ("config_manager, library tables", test_library_tables, ("wx",)),
    ("component_loader, pro result", test_pro_result, ("requests", "pcbnew")),
]


def main():
    failures = []

    for name, section, requires in SECTIONS:
        missing = [module for module in requires if not _importable(module)]

        if missing:
            skipped.append(f"{name} (needs {', '.join(missing)})")
            continue

        try:
            section()
            print(f"ok    {name}")
        except Exception as e:
            failures.append(f"{name}: {e}")
            print(f"FAIL  {name}: {e}")

    for name in skipped:
        print(f"skip  {name}")

    print(f"\n{passed} checks passed, {len(failures)} failed, {len(skipped)} sections skipped")

    return 1 if failures else 0


def _importable(module):
    try:
        __import__(module)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
