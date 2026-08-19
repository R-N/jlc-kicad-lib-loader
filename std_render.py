"""EasyEDA Std documents as inline SVG, for the preview only.

`symbolSvg` and `footprintSvg` take a Std document's `dataStr` - the classic
`~`-separated shape strings, not the Pro format `pro_render.py` speaks - and draw
it locally, so the preview can be hovered for pin and pad details and its
footprint layers switched off. EasyEDA renders every Std document as a flat PNG
(`image.easyeda.com/components/<uuid>.png`), which is what the preview shows by
default; this is what the "Render locally" button switches to.

Every field index and unit convention is copied from KiCad's own **Std**
importers - `eeschema/sch_io/easyeda/sch_easyeda_parser.cpp` and
`pcbnew/pcb_io/easyeda/pcb_io_easyeda_parser.cpp` - so the drawing agrees with
what importing the part produces:

* **Y grows down**, unlike Pro. Neither Std importer negates Y (`RelPosY` in
  `common/io/easyeda/easyeda_parser_base.cpp` is symmetric with `RelPosX`),
  because EasyEDA Std and KiCad both put Y downwards. `pro_render`'s emitter was
  written for Pro documents and negates every Y on its way out, so `_StdDrawing`
  negates once on the way in and the two cancel.
* One Std unit is **10 mil = 0.254 mm** in both domains: symbols scale by
  `schIUScale.MilsToIU(value * 10)`, footprints by
  `KiROUND(value * 254000.0 / 100.0) * 100` nanometres - the same 0.254 mm.
* Rotations are **degrees, clockwise**, applied as they come; that is also SVG's
  direction with Y down, so they pass straight through.
* A Std `A` arc, a `PT` path and a `SOLIDREGION` outline are already SVG path
  syntax in document coordinates, so they are emitted verbatim rather than
  re-derived; only their bounding box has to be walked.

Two details are drawn from the document rather than from the importer, because
they are KiCad limitations rather than properties of the part: an `E` ellipse
keeps its Y radius (KiCad draws a circle of the X radius and discards `radiusY`),
and that is the only place the two disagree on geometry.

Preview only. The download path ships documents verbatim and converts no
geometry; nothing here is used for it.
"""
import json
import math
import re

try:
    from . import pro_render
except ImportError:      # imported flat, e.g. by a test harness
    import pro_render

# 10 mil per unit, so a pad size can be shown in millimetres.
STD_UNIT_MM = 10.0 / 39.37

SYMBOL_COLOR = "#880000"
PIN_COLOR = "#880000"
PIN_TEXT_COLOR = "#0000aa"
DEFAULT_FONT = 7.0          # "7pt", the EasyEDA default when a T carries no size
FOOTPRINT_TEXT_COLOR = "#c8a415"

# What a footprint preview draws. Copper, silkscreen, board outline and
# multi-layer are the part; paste, mask, fab, assembly, document and the 3D-model
# layer are annotations EasyEDA does not draw either, and painting them buries the
# pads under opaque blocks. Layer ids are KiCad's Std table (`LayerToKi`).
ARTWORK_LAYERS = {1, 2, 3, 4, 10, 11} | set(range(21, 51))

# Fallback colours for a document whose `layers` array is missing an entry.
LAYER_COLORS = {1: "#ff0000", 2: "#0000ff", 3: "#ffcc00", 4: "#66cc33",
                10: "#ff00ff", 11: "#c0c0c0"}
LAYER_NAMES = {1: "Top Layer", 2: "Bottom Layer", 3: "Top Silkscreen",
               4: "Bottom Silkscreen", 10: "Board Outline", 11: "Multi-Layer"}

# EasyEDA's electrical types, for the pin tooltip (`ConvertElecType`).
PIN_TYPES = {"0": "Unspecified", "1": "Input", "2": "Output",
             "3": "Bidirectional", "4": "Passive"}

TEXT_ANCHORS = {"start": "start", "middle": "middle", "end": "end"}


def _num(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _at(fields, index, default=""):
    return fields[index] if len(fields) > index else default


def _mm(units):
    return f"{_num(units) * STD_UNIT_MM:.3f} mm"


def _color(value, default):
    value = (value or "").strip()

    return value if value.startswith("#") else default


class _StdDrawing(pro_render._Drawing):
    """`pro_render`'s SVG emitter, fed Std (Y-down) coordinates.

    Every override negates Y - and rotation, which the emitter also negates - so
    the emitter's Pro-oriented flip cancels out. `path` is the exception: its `d`
    is built here, already in SVG space, so only the bounding box is grown.
    """

    def line(self, x1, y1, x2, y2, color, width):
        super().line(x1, -y1, x2, -y2, color, width)

    def hit(self, x1, y1, x2, y2, width):
        super().hit(x1, -y1, x2, -y2, width)

    def circle(self, cx, cy, r, color, width=0.0, fill="none"):
        super().circle(cx, -cy, r, color, width, fill)

    def ellipse(self, cx, cy, rx, ry, rot, fill, color=None, width=0.0):
        super().ellipse(cx, -cy, rx, ry, -rot, fill, color, width)

    def rect(self, cx, cy, w, h, rot, fill, radius=0.0, color=None, width=0.0):
        super().rect(cx, -cy, w, h, -rot, fill, radius, color, width)

    def text(self, x, y, content, size, color, rot=0.0, anchor="start", baseline="auto"):
        super().text(x, -y, content, size, color, -rot, anchor, baseline)

    def growStd(self, x, y, margin=0.0):
        self.grow(x, y, margin)

    def pathBounds(self, d):
        """Grow the bounding box over an SVG path given in Std coordinates.

        Walks the commands rather than every number, so an arc's radii and flags
        are not mistaken for coordinates. An arc is bounded by its endpoints
        widened by its radius, which is loose but never clips the drawing.
        """
        tokens = re.findall(r"[MmLlHhVvAaCcSsQqTtZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d or "")
        i = 0
        command = ""
        x = y = 0.0

        def take(count):
            nonlocal i
            values = []

            while len(values) < count and i < len(tokens):
                if re.match(r"[A-Za-z]", tokens[i]):
                    return None

                values.append(float(tokens[i]))
                i += 1

            return values if len(values) == count else None

        while i < len(tokens):
            if re.match(r"[A-Za-z]", tokens[i]):
                command = tokens[i]
                i += 1
                continue

            if command in ("M", "L", "T"):
                point = take(2)

                if not point:
                    break

                x, y = point
                self.grow(x, y)
            elif command in ("C",):
                points = take(6)

                if not points:
                    break

                for j in range(0, 6, 2):
                    self.grow(points[j], points[j + 1])

                x, y = points[4], points[5]
            elif command in ("Q", "S"):
                points = take(4)

                if not points:
                    break

                for j in range(0, 4, 2):
                    self.grow(points[j], points[j + 1])

                x, y = points[2], points[3]
            elif command in ("A",):
                arc = take(7)

                if not arc:
                    break

                radius = max(abs(arc[0]), abs(arc[1]))
                self.grow(x, y, radius)
                x, y = arc[5], arc[6]
                self.grow(x, y, radius)
            elif command in ("H",):
                value = take(1)

                if not value:
                    break

                x = value[0]
                self.grow(x, y)
            elif command in ("V",):
                value = take(1)

                if not value:
                    break

                y = value[0]
                self.grow(x, y)
            else:
                # A relative command or something unexpected: stop guessing rather
                # than grow the box wrongly. The absolute prefix is already covered.
                break

    def contour(self, d, color=None, width=0.0, fill="none"):
        """One EasyEDA path, verbatim, with its bounds accounted for."""
        if not (d or "").strip():
            return

        self.pathBounds(d)
        self.elements.append(
            f'<path d="{pro_render._escape(d.strip())}" fill="{fill}"'
            + (f' stroke="{color}" stroke-width="{max(width, 0.4):.3f}"' if color else "")
            + ' stroke-linecap="round" stroke-linejoin="round"/>')

    def points(self, data, color, width, fill="none", closed=False):
        """A polyline from a space-separated `x y x y …` list."""
        values = [_num(v) for v in re.split(r"[\s,]+", (data or "").strip()) if v != ""]

        if len(values) < 4:
            return

        parts = [f"M {values[0]:.3f} {values[1]:.3f}"]

        for i in range(2, len(values) - 1, 2):
            parts.append(f"L {values[i]:.3f} {values[i + 1]:.3f}")

        if closed:
            parts.append("Z")

        for i in range(0, len(values) - 1, 2):
            self.grow(values[i], values[i + 1])

        self.elements.append(
            f'<path d="{" ".join(parts)}" fill="{fill}"'
            + (f' stroke="{color}" stroke-width="{max(width, 0.4):.3f}"' if color else "")
            + ' stroke-linecap="round" stroke-linejoin="round"/>')


def _fill(value, stroke):
    """EasyEDA's fill rule: "none" is unfilled, anything else is a colour."""
    value = (value or "none").strip().lower()

    if value in ("", "none"):
        return "none"

    return stroke if value in ("1", "true") else value


def _fontSize(raw, content):
    """A `T`/`TEXT` font size in document units, as KiCad derives it."""
    text = str(raw or "").strip()
    size = DEFAULT_FONT

    if text.lower().endswith("pt"):
        size = _num(text[:-2], DEFAULT_FONT)
    elif text:
        size = _num(text, DEFAULT_FONT)

    # KiCad: ×0.8 for multi-line text, ×0.95 otherwise.
    return (size or DEFAULT_FONT) * (0.8 if "\n" in str(content or "") else 0.95)


def _shapes(dataStr):
    if not isinstance(dataStr, dict):
        return []

    shapes = dataStr.get("shape")

    return [s for s in shapes if isinstance(s, str)] if isinstance(shapes, list) else []


def _pinSegments(shape):
    return [segment.split("~") for segment in shape.split("^^")]


def _pinLine(pathData, tipX, tipY):
    """(tip, body end) of a pin from its `M x y h|v len` path.

    KiCad decides a pin's orientation by comparing the path's start with the pin
    position and looking at the sign of the length; the same comparison gives the
    two ends directly, which is what a drawing needs.
    """
    match = re.match(r"^\s*M\s*(-?[\d.]+)[,\s]\s*(-?[\d.]+)\s*([hv])\s*(-?[\d.]+)\s*$",
                     pathData or "")

    if not match:
        return None

    startX, startY = _num(match.group(1)), _num(match.group(2))
    vertical = match.group(3) == "v"
    length = _num(match.group(4))
    endX = startX if vertical else startX + length
    endY = startY + length if vertical else startY

    # Whichever end sits on the pin position is the end a wire connects to.
    if abs(startX - tipX) < 0.01 and abs(startY - tipY) < 0.01:
        return (startX, startY), (endX, endY)

    return (endX, endY), (startX, startY)


def symbolSvg(dataStr):
    """Inline SVG of an EasyEDA Std symbol document, or "" with nothing to draw."""
    try:
        drawing = _StdDrawing()

        for shape in _shapes(dataStr):
            fields = shape.split("~")
            token = fields[0]
            mark = len(drawing.elements)

            if token == "P":
                _symbolPin(drawing, shape)
                continue

            if token in ("PL", "PG"):
                stroke = _color(_at(fields, 2), SYMBOL_COLOR)
                drawing.points(_at(fields, 1), stroke, _num(_at(fields, 3), 0.6),
                               _fill(_at(fields, 5), stroke), token == "PG")
            elif token == "PT":
                stroke = _color(_at(fields, 2), SYMBOL_COLOR)
                drawing.contour(_at(fields, 1), stroke, _num(_at(fields, 3), 0.6),
                                _fill(_at(fields, 5), stroke))
            elif token == "A":
                # The A token skips one field: its stroke colour is at 3, where
                # PL/PG/PT/R/E keep it at 2.
                stroke = _color(_at(fields, 3), SYMBOL_COLOR)
                drawing.contour(_at(fields, 1), stroke, _num(_at(fields, 4), 0.6),
                                _fill(_at(fields, 6), stroke))
            elif token == "R":
                x, y = _num(_at(fields, 1)), _num(_at(fields, 2))
                w, h = _num(_at(fields, 5)), _num(_at(fields, 6))
                stroke = _color(_at(fields, 7), SYMBOL_COLOR)
                drawing.rect(x + w / 2, y + h / 2, w, h, 0.0,
                             _fill(_at(fields, 10), stroke), 0.0, stroke,
                             _num(_at(fields, 8), 0.6))
            elif token == "E":
                stroke = _color(_at(fields, 5), SYMBOL_COLOR)
                rx = _num(_at(fields, 3))
                # KiCad discards radiusY and draws a circle; the document knows
                # better, and an ellipse drawn as a circle is a visible lie.
                ry = _num(_at(fields, 4), rx) or rx
                drawing.ellipse(_num(_at(fields, 1)), _num(_at(fields, 2)), rx, ry, 0.0,
                                _fill(_at(fields, 8), stroke), stroke,
                                _num(_at(fields, 6), 0.6))
            elif token == "T":
                if _at(fields, 13) == "0":
                    continue

                content = str(_at(fields, 12)).replace("\\n", "\n")
                size = _fontSize(_at(fields, 7), content)
                # EasyEDA's angle is clockwise, and KiCad negates it.
                drawing.text(_num(_at(fields, 2)), _num(_at(fields, 3)), content, size,
                             _color(_at(fields, 5), SYMBOL_COLOR),
                             -_num(_at(fields, 4)),
                             TEXT_ANCHORS.get(_at(fields, 14), "start"))

            drawing.wrap(mark, data_kind="shape", data_token=token)

        return drawing.svg()
    except Exception:
        return ""


def _symbolPin(drawing, shape):
    """One `P` pin: its line, a fat invisible hover target, and its texts."""
    segments = _pinSegments(shape)
    main = segments[0] if segments else []
    number = str(_at(main, 3))
    tipX, tipY = _num(_at(main, 4)), _num(_at(main, 5))
    pinType = PIN_TYPES.get(str(_at(main, 2)), "")
    nameParts = segments[3] if len(segments) > 3 else []
    numberParts = segments[4] if len(segments) > 4 else []
    name = str(_at(nameParts, 4))
    ends = _pinLine(_at(segments[2] if len(segments) > 2 else [], 0), tipX, tipY)

    if not ends:
        return

    (tx, ty), (bx, by) = ends
    mark = len(drawing.elements)
    drawing.line(tx, ty, bx, by, PIN_COLOR, 0.6)
    # A 0.6-unit line cannot be pointed at; this is the hover target.
    drawing.hit(tx, ty, bx, by, 6.0)

    if len(segments) > 5 and _at(segments[5], 0) == "1":
        # Inversion dot, drawn at the connection end.
        drawing.circle(*_dotCentre(tx, ty, bx, by), 2.0, PIN_COLOR, 0.6)

    if len(segments) > 6 and _at(segments[6], 0) == "1":
        drawing.contour(_at(segments[6], 1), PIN_COLOR, 0.6)

    step = 3.0
    dx = (1 if bx > tx else -1 if bx < tx else 0)
    dy = (1 if by > ty else -1 if by < ty else 0)

    if _at(numberParts, 0) != "0" and number:
        # Above the line for a horizontal pin, beside it for a vertical one.
        if dx:
            drawing.text((tx + bx) / 2, ty - 1.5, number, 4.5, PIN_TEXT_COLOR,
                         anchor="middle")
        else:
            drawing.text(tx + 1.5, (ty + by) / 2, number, 4.5, PIN_TEXT_COLOR)

    if _at(nameParts, 0) != "0" and name:
        anchor = "start" if dx > 0 else "end" if dx < 0 else "middle"
        drawing.text(bx + dx * step, by + dy * step + (1.5 if dy else 1.5), name, 5.0,
                     PIN_TEXT_COLOR, anchor=anchor)

    drawing.wrap(mark, data_kind="pin", data_number=number, data_name=name,
                 data_type=pinType)


def _dotCentre(tx, ty, bx, by):
    """Centre of the inversion dot: just outside the connection end."""
    length = math.hypot(bx - tx, by - ty) or 1.0

    return tx + (bx - tx) / length * 2.0, ty + (by - ty) / length * 2.0


def _layers(dataStr):
    """{id: (name, colour)} from the document's own `layers` array.

    Entries are `id~name~colour~visible~active~enabled`.
    """
    table = {}

    for line in (dataStr or {}).get("layers") or []:
        if not isinstance(line, str):
            continue

        fields = line.split("~")
        layerId = int(_num(_at(fields, 0), -1))

        if layerId < 0:
            continue

        name = _at(fields, 1) or LAYER_NAMES.get(layerId, f"Layer {layerId}")
        table[layerId] = (name.strip(), _color(_at(fields, 2), LAYER_COLORS.get(layerId, "#888888")))

    return table


def footprintSvg(dataStr):
    """Inline SVG of an EasyEDA Std footprint document, or "" if nothing draws."""
    try:
        drawing = _StdDrawing()
        table = _layers(dataStr)
        pads = []

        def layerOf(raw):
            layerId = int(_num(raw, -1))
            name, color = table.get(layerId,
                                   (LAYER_NAMES.get(layerId, f"Layer {layerId}"),
                                    LAYER_COLORS.get(layerId, "#888888")))

            return layerId, name, color

        for shape in _shapes(dataStr):
            fields = shape.split("~")
            token = fields[0]

            if token == "PAD":
                pads.append(fields)
                continue

            layerId, layerName, color = layerOf(
                _at(fields, {"TRACK": 2, "CIRCLE": 5, "RECT": 5, "ARC": 2,
                             "SOLIDREGION": 1, "COPPERAREA": 2, "TEXT": 7}.get(token, 1), -1)
                if token != "HOLE" and token != "VIA" else 11)

            if token in ("HOLE", "VIA"):
                layerId, layerName, color = layerOf(11)
            elif layerId not in ARTWORK_LAYERS:
                continue

            mark = len(drawing.elements)

            if token == "TRACK":
                drawing.points(_at(fields, 4), color, _num(_at(fields, 1), 0.6))
            elif token == "CIRCLE":
                drawing.circle(_num(_at(fields, 1)), _num(_at(fields, 2)),
                               _num(_at(fields, 3)), color, _num(_at(fields, 4), 0.6))
            elif token == "RECT":
                x, y = _num(_at(fields, 1)), _num(_at(fields, 2))
                w, h = _num(_at(fields, 3)), _num(_at(fields, 4))
                filled = (_at(fields, 9) or "none").lower() != "none"
                drawing.rect(x + w / 2, y + h / 2, w, h, 0.0, color if filled else "none",
                             0.0, color, _num(_at(fields, 8), 0.6))
            elif token == "ARC":
                drawing.contour(_at(fields, 4), color, _num(_at(fields, 1), 0.6))
            elif token == "SOLIDREGION":
                # "cutout" is a keepout, not copper: outline only.
                cutout = (_at(fields, 4) or "").lower() == "cutout"
                drawing.contour(_at(fields, 3), color, 0.6,
                                "none" if cutout else color)
            elif token == "COPPERAREA":
                drawing.contour(_at(fields, 4), color, _num(_at(fields, 1), 0.6))
            elif token == "TEXT":
                if (_at(fields, 12) or "").lower() == "none":
                    continue

                content = str(_at(fields, 10)).replace("\\n", "\n")
                drawing.text(_num(_at(fields, 2)), _num(_at(fields, 3)), content,
                             _num(_at(fields, 9), 6.0) * 0.8, color,
                             _num(_at(fields, 5)))
            elif token == "HOLE":
                # Unplated: a bare hole, no copper around it.
                drawing.circle(_num(_at(fields, 1)), _num(_at(fields, 2)),
                               _num(_at(fields, 3)), color, 0.6, "#ffffff")
            elif token == "VIA":
                cx, cy = _num(_at(fields, 1)), _num(_at(fields, 2))
                drawing.circle(cx, cy, _num(_at(fields, 3)) / 2, color, 0.0, color)
                drawing.circle(cx, cy, _num(_at(fields, 5)), color, 0.4, "#ffffff")
            elif token == "SVGNODE":
                _svgNode(drawing, _at(fields, 1), layerOf)
                continue

            drawing.wrap(mark, data_layer=layerId, data_layername=layerName)

        # Pads last, so silkscreen cannot cover them.
        for fields in pads:
            _pad(drawing, fields, layerOf)

        return drawing.svg()
    except Exception:
        return ""


def _svgNode(drawing, raw, layerOf):
    """A SVGNODE: an SVG path on a layer, or the 3D model, which is not drawable."""
    try:
        node = json.loads(raw)
    except Exception:
        return

    layerId, layerName, color = layerOf(str((node or {}).get("layerid", "")))

    if layerId == 19 or layerId not in ARTWORK_LAYERS:
        # 19 carries the STEP model reference, not geometry.
        return

    mark = len(drawing.elements)
    drawing.contour(((node or {}).get("attrs") or {}).get("d", ""), color, 0.6, color)
    drawing.wrap(mark, data_layer=layerId, data_layername=layerName)


def _pad(drawing, fields, layerOf):
    """One PAD, with the metadata the preview's tooltip reads."""
    shape = (_at(fields, 1) or "").upper()
    cx, cy = _num(_at(fields, 2)), _num(_at(fields, 3))
    w, h = _num(_at(fields, 4)), _num(_at(fields, 5))
    layerId, layerName, color = layerOf(_at(fields, 6))
    number = str(_at(fields, 8))
    holeRadius = _num(_at(fields, 9))
    rotation = _num(_at(fields, 11))
    holeLength = _num(_at(fields, 13))

    if w <= 0 and h <= 0 and shape != "POLYGON":
        # A pad with no size is a truncated or malformed record, not a picture.
        return

    mark = len(drawing.elements)

    if shape == "POLYGON":
        drawing.points(_at(fields, 10), color, 0.4, color, closed=True)
    elif shape == "RECT":
        drawing.rect(cx, cy, w, h, rotation, color)
    elif shape == "OVAL" and abs(w - h) < 0.001:
        drawing.circle(cx, cy, w / 2, color, 0.0, color)
    else:
        # ELLIPSE and a non-square OVAL are both drawn as an oval, as KiCad does.
        drawing.ellipse(cx, cy, w / 2, h / 2, rotation, color)

    if holeRadius > 0:
        if holeLength > 0:
            # A slot: the long axis follows the pad's longer dimension.
            slotW, slotH = ((holeRadius * 2, holeLength) if w < h
                            else (holeLength, holeRadius * 2))
            drawing.rect(cx, cy, slotW, slotH, rotation, "#ffffff",
                         min(slotW, slotH) / 2)
        else:
            drawing.circle(cx, cy, holeRadius, "#ffffff", 0.0, "#ffffff")

    drawing.wrap(mark, data_kind="pad", data_number=number,
                 data_size=f"{_mm(w)} \u00d7 {_mm(h)}".replace(" mm \u00d7", " \u00d7"),
                 data_drill=(f"{_mm(holeRadius * 2)}"
                             + (f" \u00d7 {_mm(holeLength)}" if holeLength > 0 else "")
                             if holeRadius > 0 else ""),
                 data_layername=layerName)
    drawing.wrap(mark, data_layer=layerId, data_layername=layerName)
