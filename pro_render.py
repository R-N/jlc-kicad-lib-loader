"""SVG rendering of EasyEDA Pro symbol and footprint documents.

EasyEDA renders drawings only for parts that carry an LCSC part number, so JLC
Public parts drawn from scratch have nothing to show in the preview pane even
though their geometry sits right there in the document. Draw it here instead.

Field positions and unit conventions follow KiCad's own importers
(eeschema/sch_io/easyedapro, pcbnew/pcb_io/easyedapro) so that the preview
agrees with what importing the part actually produces: Y grows upwards, symbol
coordinates are in 10-mil units, footprint coordinates are in mils, and a
footprint document carries its own layer palette.

Rendering must never break the preview: a document that cannot be drawn yields
an empty string and the caller falls back to its own message.
"""

import json
import math

from logging import warning

# Longest edge of a rendered drawing, in px. The preview pane caps images at
# 220px anyway; matching it keeps text legible instead of downscaled to mush.
PREVIEW_PX = 220

# Body and pin colour of the EasyEDA symbol editor.
SYMBOL_COLOR = "#800000"
PIN_TEXT_COLOR = "#000000"

# EasyEDA Pro's default font size, in document units; `null` in a FONTSTYLE.
DEFAULT_FONT_SIZE = 7

# KiCad applies this to a FONTSTYLE size to get the cap height.
FONT_CAP_RATIO = 0.62

TEXT_ANCHORS = {0: "start", 1: "middle", 2: "end"}
TEXT_BASELINES = {0: "hanging", 1: "central", 2: "auto"}

# Footprint layer ids that carry visible artwork, numbered as in KiCad's
# PCB_IO_EASYEDAPRO_PARSER::LayerToKi: copper 1/2 and inner 15-44, silkscreen
# 3/4, board outline 11, multi-layer 12. Everything else (solder mask and
# paste, fab and assembly, component shape and marking, pin soldering and
# floating, keepout and restrict, 3D shells) is documentation that EasyEDA does
# not draw either, and drawing it buries the pads under opaque blocks.
ARTWORK_LAYERS = frozenset({1, 2, 3, 4, 11, 12} | set(range(15, 45)))


def parseLines(dataStr):
    """The JSON array per line of a Pro document, skipping anything unparseable."""
    lines = []

    for line in (dataStr or "").splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            shape = json.loads(line)
        except ValueError:
            continue

        if isinstance(shape, list) and shape and isinstance(shape[0], str):
            lines.append(shape)

    return lines


def _num(value, default=0.0):
    return float(value) if isinstance(value, (int, float)) else default


def _at(shape, index, default=None):
    return shape[index] if len(shape) > index else default


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


class _Drawing:
    """Collects SVG elements and the bounding box they occupy.

    Coordinates go in as EasyEDA document units (Y up) and come out as SVG user
    units (Y down); the caller only ever passes document coordinates.
    """

    def __init__(self):
        self.elements = []
        self.min_x = self.min_y = math.inf
        self.max_x = self.max_y = -math.inf

    def grow(self, x, y, margin=0.0):
        self.min_x = min(self.min_x, x - margin)
        self.max_x = max(self.max_x, x + margin)
        self.min_y = min(self.min_y, y - margin)
        self.max_y = max(self.max_y, y + margin)

    def line(self, x1, y1, x2, y2, color, width):
        self.grow(x1, -y1)
        self.grow(x2, -y2)
        self.elements.append(
            f'<line x1="{x1:.3f}" y1="{-y1:.3f}" x2="{x2:.3f}" y2="{-y2:.3f}"'
            f' stroke="{color}" stroke-width="{max(width, 0.4):.3f}"/>')

    def circle(self, cx, cy, r, color, width=0.0, fill="none"):
        self.grow(cx, -cy, r)
        stroke = f' stroke="{color}" stroke-width="{max(width, 0.4):.3f}"' if width or fill == "none" else ""
        self.elements.append(
            f'<circle cx="{cx:.3f}" cy="{-cy:.3f}" r="{r:.3f}" fill="{fill}"{stroke}/>')

    def ellipse(self, cx, cy, rx, ry, rot, fill):
        self.grow(cx, -cy, max(rx, ry))
        transform = f' transform="rotate({-rot:.3f} {cx:.3f} {-cy:.3f})"' if rot else ""
        self.elements.append(
            f'<ellipse cx="{cx:.3f}" cy="{-cy:.3f}" rx="{rx:.3f}" ry="{ry:.3f}"'
            f' fill="{fill}"{transform}/>')

    def rect(self, cx, cy, w, h, rot, fill, radius=0.0, color=None, width=0.0):
        self.grow(cx, -cy, max(abs(w), abs(h)) / 2)
        transform = f' transform="rotate({-rot:.3f} {cx:.3f} {-cy:.3f})"' if rot else ""
        rounded = f' rx="{radius:.3f}"' if radius else ""
        stroke = f' stroke="{color}" stroke-width="{max(width, 0.4):.3f}"' if color else ""
        self.elements.append(
            f'<rect x="{cx - w / 2:.3f}" y="{-cy - h / 2:.3f}" width="{abs(w):.3f}"'
            f' height="{abs(h):.3f}"{rounded} fill="{fill}"{stroke}{transform}/>')

    def path(self, d, color=None, width=0.0, fill="none"):
        if not d:
            return

        stroke = f' stroke="{color}" stroke-width="{max(width, 0.4):.3f}"' if color else ""
        self.elements.append(
            f'<path d="{d}" fill="{fill}"{stroke} stroke-linecap="round"'
            ' stroke-linejoin="round"/>')

    def text(self, x, y, content, size, color, rot=0.0, anchor="start", baseline="auto"):
        content = str(content)

        if not content.strip():
            return

        # Text is not measured, only estimated, so that a long pin name still
        # lands inside the viewBox instead of being clipped at the edge.
        width = len(content) * size * 0.6
        offset = {"start": (0, width), "middle": (width / 2, width / 2), "end": (width, 0)}[anchor]
        self.grow(x - offset[0], -y)
        self.grow(x + offset[1], -y - size)
        self.grow(x, -y + size)

        transform = f' transform="rotate({-rot:.3f} {x:.3f} {-y:.3f})"' if rot else ""
        self.elements.append(
            f'<text x="{x:.3f}" y="{-y:.3f}" font-family="Arial, Helvetica, sans-serif"'
            f' font-size="{size:.3f}" fill="{color}" text-anchor="{anchor}"'
            f' dominant-baseline="{baseline}"{transform}>{_escape(content)}</text>')

    def contour(self, data, closed=False):
        """An SVG path from an EasyEDA point/primitive list.

        Mirrors KiCad's PCB_IO_EASYEDAPRO_PARSER::ParseContour. Full-circle and
        rectangle primitives are separate SVG elements, so they are returned for
        the caller to draw with its own fill and stroke.
        """
        d = []
        primitives = []
        x = y = 0.0
        started = False
        i = 0

        def moveTo(px, py):
            self.grow(px, -py)
            d.append(f"M {px:.3f} {-py:.3f}")

        def lineTo(px, py):
            self.grow(px, -py)
            d.append(f"L {px:.3f} {-py:.3f}")

        while i < len(data):
            token = data[i]

            if isinstance(token, (int, float)):
                if i + 1 >= len(data):
                    break

                x, y = _num(token), _num(data[i + 1])
                i += 2
                continue

            if not isinstance(token, str):
                i += 1
                continue

            if token == "L":
                if not started:
                    moveTo(x, y)
                    started = True

                i += 1

                while i + 1 < len(data) and isinstance(data[i], (int, float)):
                    x, y = _num(data[i]), _num(data[i + 1])
                    lineTo(x, y)
                    i += 2

                continue

            if token in ("ARC", "CARC"):
                angle = _num(_at(data, i + 1))
                ex, ey = _num(_at(data, i + 2)), _num(_at(data, i + 3))

                if not started:
                    moveTo(x, y)
                    started = True

                # EasyEDA stores the included angle; SVG wants a radius plus
                # large-arc/sweep flags. Y is flipped, so the sweep flag keeps
                # the sign that KiCad treats as clockwise.
                half = math.radians(abs(angle) / 2.0)
                chord = math.hypot(ex - x, ey - y)

                if chord > 0 and math.sin(half) > 1e-9:
                    radius = chord / (2.0 * math.sin(half))
                    large = 1 if abs(angle) > 180 else 0
                    sweep = 1 if angle >= 0 else 0
                    self.grow(ex, -ey)
                    self.grow(x, -y, radius * 0.15)
                    d.append(f"A {radius:.3f} {radius:.3f} 0 {large} {sweep}"
                             f" {ex:.3f} {-ey:.3f}")
                else:
                    lineTo(ex, ey)

                x, y = ex, ey
                i += 4
                continue

            if token == "C":
                pts = [_num(_at(data, i + n)) for n in range(1, 7)]

                if not started:
                    moveTo(x, y)
                    started = True

                for n in range(0, 6, 2):
                    self.grow(pts[n], -pts[n + 1])

                d.append(f"C {pts[0]:.3f} {-pts[1]:.3f} {pts[2]:.3f} {-pts[3]:.3f}"
                         f" {pts[4]:.3f} {-pts[5]:.3f}")
                x, y = pts[4], pts[5]
                i += 7
                continue

            if token == "CIRCLE":
                primitives.append(("CIRCLE", _num(_at(data, i + 1)), _num(_at(data, i + 2)),
                                   _num(_at(data, i + 3))))
                i += 4
                continue

            if token == "R":
                sx, sy = _num(_at(data, i + 1)), _num(_at(data, i + 2))
                w, hgt = _num(_at(data, i + 3)), _num(_at(data, i + 4))
                angle = _num(_at(data, i + 5))
                radius = _num(_at(data, i + 6))
                # The stored point is a corner; rotation happens about it.
                cx = sx + w / 2.0
                cy = sy + hgt / 2.0
                rad = math.radians(angle)
                rx = sx + (cx - sx) * math.cos(rad) - (cy - sy) * math.sin(rad)
                ry = sy + (cx - sx) * math.sin(rad) + (cy - sy) * math.cos(rad)
                primitives.append(("R", rx, ry, w, hgt, angle, radius))
                i += 7
                continue

            i += 1

        if closed and d:
            d.append("Z")

        return " ".join(d), primitives

    def primitives(self, primitives, color, width, fill):
        for prim in primitives:
            if prim[0] == "CIRCLE":
                _, cx, cy, r = prim
                self.circle(cx, cy, r, color, width, fill)
            else:
                _, cx, cy, w, h, angle, radius = prim
                self.rect(cx, cy, w, h, angle, fill if fill != "none" else "none",
                          radius, None if fill != "none" else color, width)

    def svg(self):
        """The finished <svg>, or "" when nothing was drawn."""
        if not self.elements or self.min_x > self.max_x:
            return ""

        # A margin keeps strokes and estimated text extents off the edge.
        span_x = max(self.max_x - self.min_x, 1e-6)
        span_y = max(self.max_y - self.min_y, 1e-6)
        margin = max(span_x, span_y) * 0.04
        span_x += margin * 2
        span_y += margin * 2

        scale = PREVIEW_PX / max(span_x, span_y)
        body = "".join(self.elements)

        return (f'<svg xmlns="http://www.w3.org/2000/svg"'
                f' width="{span_x * scale:.0f}" height="{span_y * scale:.0f}"'
                f' viewBox="{self.min_x - margin:.3f} {self.min_y - margin:.3f}'
                f' {span_x:.3f} {span_y:.3f}">{body}</svg>')


def _fontStyles(lines):
    return {style[1]: style for style in lines
            if style[0] == "FONTSTYLE" and len(style) > 1 and isinstance(style[1], str)}


def _fontOf(fontStyles, styleId):
    """(size, colour, anchor, baseline) of a FONTSTYLE, with EasyEDA's defaults."""
    style = fontStyles.get(styleId) or []
    size = _num(_at(style, 5), DEFAULT_FONT_SIZE) * FONT_CAP_RATIO
    color = _at(style, 3) if isinstance(_at(style, 3), str) else PIN_TEXT_COLOR
    valign = _at(style, 10)
    halign = _at(style, 11)

    return (size or DEFAULT_FONT_SIZE * FONT_CAP_RATIO, color,
            TEXT_ANCHORS.get(halign, "start"), TEXT_BASELINES.get(valign, "auto"))


def _lineWidth(lineStyles, styleId, default=0.6):
    style = lineStyles.get(styleId) or []
    return _num(_at(style, 3), default) or default


def symbolSvg(dataStr):
    """Inline SVG of a Pro symbol document, or "" if there is nothing to draw."""
    try:
        lines = parseLines(dataStr)
        drawing = _Drawing()
        fontStyles = _fontStyles(lines)
        lineStyles = {s[1]: s for s in lines
                      if s[0] == "LINESTYLE" and len(s) > 1 and isinstance(s[1], str)}
        pins = {}

        for shape in lines:
            kind = shape[0]

            if kind == "POLY":
                points = _at(shape, 2) or []
                closed = bool(_at(shape, 3))
                width = _lineWidth(lineStyles, _at(shape, 4))
                d = []

                for n in range(0, len(points) - 1, 2):
                    px, py = _num(points[n]), _num(points[n + 1])
                    drawing.grow(px, -py)
                    d.append(f'{"M" if n == 0 else "L"} {px:.3f} {-py:.3f}')

                if closed and d:
                    d.append("Z")

                drawing.path(" ".join(d), SYMBOL_COLOR, width)

            elif kind == "RECT":
                x1, y1 = _num(_at(shape, 2)), _num(_at(shape, 3))
                x2, y2 = _num(_at(shape, 4)), _num(_at(shape, 5))
                drawing.rect((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, 0, "none",
                             0, SYMBOL_COLOR, _lineWidth(lineStyles, _at(shape, 9)))

            elif kind == "CIRCLE":
                drawing.circle(_num(_at(shape, 2)), _num(_at(shape, 3)), _num(_at(shape, 4)),
                               SYMBOL_COLOR, _lineWidth(lineStyles, _at(shape, 5)))

            elif kind == "ARC":
                sx, sy = _num(_at(shape, 2)), _num(_at(shape, 3))
                mx, my = _num(_at(shape, 4)), _num(_at(shape, 5))
                ex, ey = _num(_at(shape, 6)), _num(_at(shape, 7))
                drawing.path(_arcThroughPoints(drawing, sx, sy, mx, my, ex, ey),
                             SYMBOL_COLOR, _lineWidth(lineStyles, _at(shape, 8)))

            elif kind == "BEZIER":
                points = _at(shape, 2) or []

                if len(points) >= 8:
                    for n in range(0, 8, 2):
                        drawing.grow(_num(points[n]), -_num(points[n + 1]))

                    drawing.path(
                        f"M {_num(points[0]):.3f} {-_num(points[1]):.3f}"
                        f" C {_num(points[2]):.3f} {-_num(points[3]):.3f}"
                        f" {_num(points[4]):.3f} {-_num(points[5]):.3f}"
                        f" {_num(points[6]):.3f} {-_num(points[7]):.3f}",
                        SYMBOL_COLOR, _lineWidth(lineStyles, _at(shape, 3)))

            elif kind == "PIN":
                pins[_at(shape, 1)] = shape

            elif kind == "TEXT":
                size, color, anchor, baseline = _fontOf(fontStyles, _at(shape, 6))
                drawing.text(_num(_at(shape, 2)), _num(_at(shape, 3)), _at(shape, 5) or "",
                             size, color, _num(_at(shape, 4)), anchor, baseline)

        # Pins carry their name and number as child ATTRs, so draw them after the
        # pin lines and skip the ones EasyEDA marks invisible.
        for shape in lines:
            if shape[0] != "ATTR":
                continue

            if not _at(shape, 6) or not str(_at(shape, 4) or "").strip():
                continue

            x, y = _at(shape, 7), _at(shape, 8)

            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue

            size, color, anchor, baseline = _fontOf(fontStyles, _at(shape, 10))
            drawing.text(_num(x), _num(y), _at(shape, 4), size, color,
                         _num(_at(shape, 9)), anchor, baseline)

        for pin in pins.values():
            x, y = _num(_at(pin, 4)), _num(_at(pin, 5))
            length = _num(_at(pin, 6))
            rotation = _num(_at(pin, 7))
            # The stored point is the connection tip; the rotation points from
            # there back towards the body.
            rad = math.radians(rotation)
            ex = x + length * math.cos(rad)
            ey = y + length * math.sin(rad)
            drawing.line(x, y, ex, ey, SYMBOL_COLOR, 0.6)

            if _at(pin, 9) == 2:
                # Inverted pin: EasyEDA draws a bubble at the body end.
                drawing.circle(ex - 1.5 * math.cos(rad), ey - 1.5 * math.sin(rad), 1.5,
                               SYMBOL_COLOR, 0.6)

        return drawing.svg()
    except Exception as e:
        warning(f"Could not render Pro symbol preview: {e}")
        return ""


def _arcThroughPoints(drawing, sx, sy, mx, my, ex, ey):
    """SVG arc path through three points, as symbol ARCs are stored."""
    drawing.grow(sx, -sy)
    drawing.grow(mx, -my)
    drawing.grow(ex, -ey)

    # Circumcentre of the three points.
    d = 2 * (sx * (my - ey) + mx * (ey - sy) + ex * (sy - my))

    if abs(d) < 1e-9:
        return f"M {sx:.3f} {-sy:.3f} L {ex:.3f} {-ey:.3f}"

    ux = ((sx * sx + sy * sy) * (my - ey) + (mx * mx + my * my) * (ey - sy)
          + (ex * ex + ey * ey) * (sy - my)) / d
    uy = ((sx * sx + sy * sy) * (ex - mx) + (mx * mx + my * my) * (sx - ex)
          + (ex * ex + ey * ey) * (mx - sx)) / d
    radius = math.hypot(sx - ux, sy - uy)

    # Cross product of start->mid and mid->end gives the turn direction; with Y
    # flipped for SVG the sweep flag is its negation.
    cross = (mx - sx) * (ey - my) - (my - sy) * (ex - mx)
    sweep = 0 if cross > 0 else 1
    # The arc is the long way round when the centre lies on the far side of the
    # start-end chord from the midpoint.
    side_mid = (ex - sx) * (my - sy) - (ey - sy) * (mx - sx)
    side_centre = (ex - sx) * (uy - sy) - (ey - sy) * (ux - sx)
    large = 1 if side_mid * side_centre > 0 else 0

    return (f"M {sx:.3f} {-sy:.3f} A {radius:.3f} {radius:.3f} 0 {large} {sweep}"
            f" {ex:.3f} {-ey:.3f}")


def footprintSvg(dataStr):
    """Inline SVG of a Pro footprint document, or "" if there is nothing to draw."""
    try:
        lines = parseLines(dataStr)
        drawing = _Drawing()
        colors = {}

        for shape in lines:
            if shape[0] == "LAYER" and isinstance(_at(shape, 1), int):
                colors[shape[1]] = _at(shape, 5) if isinstance(_at(shape, 5), str) else "#888888"

        def colorOf(layer, default="#888888"):
            return colors.get(layer, default)

        # Pads go on top: a document may list them before the silkscreen that
        # would otherwise cover them. Everything else in a footprint document is
        # rules, panelisation and editor state, which carry no drawing.
        drawn = ("POLY", "FILL", "ATTR")
        ordered = ([s for s in lines if s[0] in drawn]
                   + [s for s in lines if s[0] == "PAD"])

        for shape in ordered:
            kind = shape[0]
            layer = _at(shape, 4)

            if not isinstance(layer, int) or layer not in ARTWORK_LAYERS:
                continue

            if kind == "PAD":
                cx, cy = _num(_at(shape, 6)), _num(_at(shape, 7))
                rotation = _num(_at(shape, 8))
                hole = _at(shape, 9) or []
                padShape = _at(shape, 10) or []
                color = colorOf(layer, "#c0c0c0")
                name = str(_at(shape, 5) or "")
                size = 0.0

                if padShape and padShape[0] == "RECT":
                    w, h = _num(_at(padShape, 1)), _num(_at(padShape, 2))
                    radius = _num(_at(padShape, 3)) / 100.0 * min(abs(w), abs(h)) / 2
                    drawing.rect(cx, cy, w, h, rotation, color, radius)
                    size = min(abs(w), abs(h))
                elif padShape and padShape[0] in ("ELLIPSE", "OVAL"):
                    w, h = _num(_at(padShape, 1)), _num(_at(padShape, 2))

                    if padShape[0] == "OVAL" and abs(w - h) > 1e-9:
                        drawing.rect(cx, cy, w, h, rotation, color, min(abs(w), abs(h)) / 2)
                    else:
                        drawing.ellipse(cx, cy, abs(w) / 2, abs(h) / 2, rotation, color)

                    size = min(abs(w), abs(h))
                elif padShape and padShape[0] in ("POLY", "POLYGON"):
                    d, primitives = drawing.contour(_at(padShape, 1) or [], closed=True)
                    drawing.path(d, fill=color)
                    drawing.primitives(primitives, color, 0, color)

                if hole and hole[0] in ("ROUND", "SLOT"):
                    dx, dy = _num(_at(hole, 1)), _num(_at(hole, 2))

                    if dx > 0 or dy > 0:
                        if hole[0] == "SLOT" and abs(dx - dy) > 1e-9:
                            drawing.rect(cx, cy, dx, dy, _num(_at(shape, 14)), "#ffffff",
                                         min(dx, dy) / 2)
                        else:
                            drawing.circle(cx, cy, max(dx, dy) / 2, color, 0, "#ffffff")

                if name and size:
                    drawing.text(cx, cy, name, min(size * 0.5, 40), "#202020",
                                 anchor="middle", baseline="central")

            elif kind == "POLY":
                width = _num(_at(shape, 5), 1.0)
                d, primitives = drawing.contour(_at(shape, 6) or [])
                color = colorOf(layer)
                drawing.path(d, color, width)
                drawing.primitives(primitives, color, width, "none")

            elif kind == "FILL":
                color = colorOf(layer)
                polyList = _at(shape, 7) or []

                if polyList and not isinstance(polyList[0], list):
                    polyList = [polyList]

                for polyData in polyList:
                    d, primitives = drawing.contour(polyData, closed=True)
                    drawing.path(d, fill=color)
                    drawing.primitives(primitives, color, 0, color)

            elif kind == "ATTR":
                if not _at(shape, 10):
                    continue

                value = _at(shape, 8)

                if not str(value or "").strip():
                    continue

                x, y = _at(shape, 5), _at(shape, 6)

                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    continue

                drawing.text(_num(x), _num(y), value, _num(_at(shape, 12), 40) * FONT_CAP_RATIO,
                             colorOf(layer), _num(_at(shape, 17)))

        return drawing.svg()
    except Exception as e:
        warning(f"Could not render Pro footprint preview: {e}")
        return ""
