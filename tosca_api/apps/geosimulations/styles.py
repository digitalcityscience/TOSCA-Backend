"""MapLibre styles and legends for simulation map layers.

A style is a list of MapLibre style layers *without* ``source``/``source-layer``;
the frontend adds the PMTiles source and sets ``source-layer`` to the output role.
Each style comes with a structured legend built from the same classes, because
a legend derived from a style's colours alone cannot say what a colour means.

Producers choose a template with the manifest's ``style_hint``:

=========================  =====================================================
``graduated:<attr>``       colour (and line width) classes of a numeric attribute
``choropleth:<attr>``      polygon fill classes (hexagon grids, districts)
``proportional:<attr>``    point radius / line width scaled continuously
``categorical:<attr>``     one colour per value (up to 10, the rest grey)
``heatmap:<attr>``         MapLibre heatmap weighted by a numeric attribute
``diverging:<attr>``       changes around zero (comparisons): decreases blue,
                           increases red, small changes faint
``simple`` / missing       one colour per geometry type
=========================  =====================================================

Classes use rounded logarithmic breaks for long-tailed data (counts such as
cyclists per segment) and quantiles otherwise.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_COLOR = "#2563eb"
OUTLINE_COLOR = "#1e3a8a"
OTHER_COLOR = "#9ca3af"
# Sequential, light to dark (ColorBrewer YlOrRd, 5 classes).
SEQUENTIAL = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]
# Categorical (Tableau 10).
CATEGORICAL = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
]
# Diverging, decrease to increase (ColorBrewer RdBu, reversed); the middle class
# (little or no change) is a faint neutral.
DIVERGING = ["#2166ac", "#92c5de", "#d1d5db", "#f4a582", "#b2182b"]
HEATMAP_RAMP = ["rgba(33,102,172,0)", "#67a9cf", "#d1e5f0", "#fddbc7", "#ef8a62", "#b2182b"]
LINE_WIDTHS = [1, 2, 3, 4.5, 6]
MAX_CLASSES = 5
MAX_CATEGORIES = len(CATEGORICAL)
HINT_KINDS = {"graduated", "choropleth", "proportional", "categorical", "heatmap", "diverging", "simple"}
NUMERIC_KINDS = {"graduated", "choropleth", "proportional", "heatmap", "diverging"}


@dataclass
class StyleResult:
    style: list[dict]
    legend: dict
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AttributeValues:
    name: str
    values: list
    numeric: bool


def parse_style_hint(hint: str | None) -> tuple[str, str | None]:
    if not hint:
        return "simple", None
    kind, _, attribute = hint.partition(":")
    return kind.strip(), (attribute.strip() or None)


def read_attribute(gpkg: Path, layer: str, attribute: str) -> AttributeValues | None:
    """Non-null values of one attribute, read with sqlite3 (a GeoPackage is SQLite)."""
    with closing(sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)) as connection:
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{layer}")')}
        if attribute not in columns:
            return None
        values = [
            row[0]
            for row in connection.execute(
                f'SELECT "{attribute}" FROM "{layer}" WHERE "{attribute}" IS NOT NULL'
            )
        ]
    numeric = bool(values) and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in values
    )
    return AttributeValues(attribute, values, numeric)


def build_style(
    output: dict, gpkg: Path, *, labels: dict | None = None
) -> StyleResult:
    """Style and legend for a manifest ``vector_layer`` output."""
    role, title, geometry = output["role"], output["title"], output["geometry_type"]
    kind, attribute = parse_style_hint(output.get("style_hint"))
    warnings: list[str] = []
    if kind not in HINT_KINDS:
        warnings.append(f"{role}: unknown style hint {output.get('style_hint')!r}; using a plain style.")
        return _with_warnings(simple_style(role, title, geometry), warnings)
    if kind == "simple":
        return simple_style(role, title, geometry)
    if attribute is None:
        warnings.append(f"{role}: style hint {kind!r} needs an attribute; using a plain style.")
        return _with_warnings(simple_style(role, title, geometry), warnings)

    values = read_attribute(gpkg, output["layer"], attribute)
    if values is None or not values.values:
        warnings.append(f"{role}: attribute {attribute!r} is missing or empty; using a plain style.")
        return _with_warnings(simple_style(role, title, geometry), warnings)
    if kind in NUMERIC_KINDS and not values.numeric:
        warnings.append(f"{role}: {kind} needs numbers but {attribute!r} is not numeric; using categories.")
        kind = "categorical"

    base = geometry.removeprefix("Multi")
    if kind == "heatmap" and base != "Point":
        warnings.append(f"{role}: heatmaps need points; using graduated colours.")
        kind = "graduated"
    if kind == "proportional" and base == "Polygon":
        warnings.append(f"{role}: polygons cannot be proportional; using a choropleth.")
        kind = "choropleth"

    if kind == "categorical":
        result = categorical_style(role, title, base, values, labels or {})
    elif kind == "diverging":
        result = diverging_style(role, title, base, values)
    elif kind == "heatmap":
        result = heatmap_style(role, title, values)
    elif kind == "proportional":
        result = proportional_style(role, title, base, values)
    else:
        result = graduated_style(role, title, base, values)
    return _with_warnings(result, warnings)


def _with_warnings(result: StyleResult, warnings: list[str]) -> StyleResult:
    result.warnings = warnings + result.warnings
    return result


# -- classes ------------------------------------------------------------------


def _nice(value: float) -> float:
    """Round to the nearest 1-2-5 step on a log scale (3.4 -> 5, 138 -> 100)."""
    if value <= 0:
        return 0
    exponent = math.floor(math.log10(value))
    candidates = [step * 10**power for power in (exponent - 1, exponent, exponent + 1) for step in (1, 2, 5)]
    return min(candidates, key=lambda candidate: abs(math.log10(candidate) - math.log10(value)))


def class_breaks(values: list[float]) -> list[float]:
    """Upper-exclusive class starts after the first class (at most MAX_CLASSES - 1)."""
    distinct = sorted(set(values))
    if len(distinct) < 2:
        return []
    low, high = distinct[0], distinct[-1]
    cuts = statistics.quantiles(values, n=MAX_CLASSES, method="inclusive")
    positive_low = min((value for value in distinct if value > 0), default=None)
    long_tail = low >= 0 and positive_low is not None and high / max(cuts[-1], positive_low) > 5
    if long_tail:
        ratio = (high / positive_low) ** (1 / MAX_CLASSES)
        cuts = [_nice(positive_low * ratio**step) for step in range(1, MAX_CLASSES)]
    breaks = sorted({cut for cut in cuts if low < cut <= high})
    return breaks[: MAX_CLASSES - 1]


def _format(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.3g}" if abs(value) < 1000 else f"{value:,.0f}"


def _class_items(breaks: list[float], colors: list[str], minimum: float, maximum: float) -> list[dict]:
    bounds = [minimum, *breaks, maximum]
    items = []
    for index, color in enumerate(colors):
        lower, upper = bounds[index], bounds[index + 1]
        if index == 0 and breaks:
            label = f"< {_format(breaks[0])}"
        elif index == len(colors) - 1 and breaks:
            label = f"≥ {_format(lower)}"
        else:
            label = f"{_format(lower)} – {_format(upper)}"
        items.append({"color": color, "label": label, "min": lower, "max": upper})
    return items


def _palette(count: int) -> list[str]:
    """``count`` sequential colours spread over the palette (always including the darkest)."""
    if count == 1:
        return [SEQUENTIAL[-2]]
    return [SEQUENTIAL[round(index * (len(SEQUENTIAL) - 1) / (count - 1))] for index in range(count)]


def _step(attribute: str, outputs: list, breaks: list[float]) -> list | str | float:
    if not breaks:
        return outputs[0]
    expression: list = ["step", ["to-number", ["get", attribute], 0], outputs[0]]
    for value, output in zip(breaks, outputs[1:], strict=True):
        expression += [value, output]
    return expression


# -- templates ----------------------------------------------------------------


def _legend_meta(title: str) -> dict:
    return {"tosca:legend-label": title}


def simple_style(role: str, title: str, geometry_type: str) -> StyleResult:
    base = geometry_type.removeprefix("Multi")
    legend = {"type": "simple", "title": title, "geometry": base, "items": [{"color": DEFAULT_COLOR, "label": title}]}
    return StyleResult(default_style(role, geometry_type, title=title), legend)


def default_style(role: str, geometry_type: str, *, title: str | None = None) -> list[dict]:
    metadata = _legend_meta(title) if title else {}
    base = geometry_type.removeprefix("Multi")
    if base == "Point":
        layers = [
            {
                "id": f"{role}-circle",
                "type": "circle",
                "paint": {
                    "circle-radius": 4,
                    "circle-color": DEFAULT_COLOR,
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 1,
                },
            }
        ]
    elif base == "LineString":
        layers = [
            {
                "id": f"{role}-line",
                "type": "line",
                "layout": {"line-cap": "round", "line-join": "round"},
                "paint": {"line-color": DEFAULT_COLOR, "line-width": 2},
            }
        ]
    else:
        layers = [
            {
                "id": f"{role}-fill",
                "type": "fill",
                "paint": {"fill-color": DEFAULT_COLOR, "fill-opacity": 0.35},
            },
            {
                "id": f"{role}-outline",
                "type": "line",
                "paint": {"line-color": OUTLINE_COLOR, "line-width": 1},
            },
        ]
    for layer in layers:
        if metadata:
            layer["metadata"] = dict(metadata)
    return layers


def graduated_style(role: str, title: str, base: str, values: AttributeValues) -> StyleResult:
    attribute = values.name
    breaks = class_breaks(values.values)
    colors = _palette(len(breaks) + 1)
    color = _step(attribute, colors, breaks)
    metadata = _legend_meta(title)
    if base == "LineString":
        widths = LINE_WIDTHS[-len(colors):] if len(colors) > 1 else [3]
        width = _step(attribute, widths, breaks)
        style = [
            {
                "id": f"{role}-line",
                "type": "line",
                "metadata": metadata,
                "layout": {
                    "line-cap": "round",
                    "line-join": "round",
                    "line-sort-key": ["to-number", ["get", attribute], 0],
                },
                "paint": {
                    "line-color": color,
                    "line-width": [
                        "interpolate", ["linear"], ["zoom"],
                        8, ["*", 0.5, width],
                        14, ["*", 1.5, width],
                    ],
                },
            }
        ]
    elif base == "Point":
        style = [
            {
                "id": f"{role}-circle",
                "type": "circle",
                "metadata": metadata,
                "layout": {"circle-sort-key": ["to-number", ["get", attribute], 0]},
                "paint": {
                    "circle-radius": 5,
                    "circle-color": color,
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 1,
                },
            }
        ]
    else:
        return choropleth_style(role, title, values, breaks=breaks)
    legend = {
        "type": "graduated",
        "title": title,
        "attribute": attribute,
        "geometry": base,
        "items": _class_items(breaks, colors, min(values.values), max(values.values)),
    }
    return StyleResult(style, legend)


def choropleth_style(
    role: str, title: str, values: AttributeValues, *, breaks: list[float] | None = None
) -> StyleResult:
    attribute = values.name
    breaks = class_breaks(values.values) if breaks is None else breaks
    colors = _palette(len(breaks) + 1)
    metadata = _legend_meta(title)
    style = [
        {
            "id": f"{role}-fill",
            "type": "fill",
            "metadata": metadata,
            "paint": {"fill-color": _step(attribute, colors, breaks), "fill-opacity": 0.7},
        },
        {
            "id": f"{role}-outline",
            "type": "line",
            "metadata": metadata,
            "paint": {"line-color": "#ffffff", "line-width": 0.5, "line-opacity": 0.8},
        },
    ]
    legend = {
        "type": "graduated",
        "title": title,
        "attribute": attribute,
        "geometry": "Polygon",
        "items": _class_items(breaks, colors, min(values.values), max(values.values)),
    }
    return StyleResult(style, legend)


def proportional_style(role: str, title: str, base: str, values: AttributeValues) -> StyleResult:
    attribute = values.name
    low, high = min(values.values), max(values.values)
    metadata = _legend_meta(title)
    color = SEQUENTIAL[3]
    if base == "LineString":
        size_property, sizes = "line-width", (1, 10)
        style_layer = {
            "id": f"{role}-line",
            "type": "line",
            "metadata": metadata,
            "layout": {"line-cap": "round", "line-sort-key": ["to-number", ["get", attribute], 0]},
            "paint": {"line-color": color},
        }
    else:
        size_property, sizes = "circle-radius", (3, 18)
        style_layer = {
            "id": f"{role}-circle",
            "type": "circle",
            "metadata": metadata,
            # Small circles on top so large ones do not hide them.
            "layout": {"circle-sort-key": ["-", 0, ["to-number", ["get", attribute], 0]]},
            "paint": {
                "circle-color": color,
                "circle-opacity": 0.75,
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 1,
            },
        }
    if high > low:
        # Area-proportional circles: radius grows with the square root of the value.
        value = ["to-number", ["get", attribute], low]
        scaled = ["sqrt", ["-", value, low]] if size_property == "circle-radius" else ["-", value, low]
        span = math.sqrt(high - low) if size_property == "circle-radius" else high - low
        size = ["interpolate", ["linear"], scaled, 0, sizes[0], span, sizes[1]]
    else:
        size = sizes[0]
    style_layer["paint"][size_property] = size
    samples = sorted({low, statistics.median(values.values), high})
    legend = {
        "type": "proportional",
        "title": title,
        "attribute": attribute,
        "geometry": base,
        "color": color,
        "items": [
            {"value": sample, "label": _format(sample), "size": _size_at(sample, low, high, sizes, size_property)}
            for sample in samples
        ],
    }
    return StyleResult([style_layer], legend)


def _size_at(value: float, low: float, high: float, sizes: tuple, size_property: str) -> float:
    if high <= low:
        return sizes[0]
    if size_property == "circle-radius":
        fraction = math.sqrt(value - low) / math.sqrt(high - low)
    else:
        fraction = (value - low) / (high - low)
    return round(sizes[0] + fraction * (sizes[1] - sizes[0]), 1)


def categorical_style(
    role: str, title: str, base: str, values: AttributeValues, labels: dict
) -> StyleResult:
    attribute = values.name
    counts: dict = {}
    for value in values.values:
        counts[value] = counts.get(value, 0) + 1
    categories = sorted(counts, key=lambda value: (-counts[value], str(value)))[:MAX_CATEGORIES]
    colors = dict(zip(categories, CATEGORICAL, strict=False))
    # MapLibre allows only strings and integers as match labels, so anything else
    # (decimals, mixed types) is matched by its string form.
    as_text = not all(isinstance(value, (str, int)) for value in categories) or len(
        {type(value) for value in categories}
    ) > 1
    match: list = ["match", ["to-string", ["get", attribute]] if as_text else ["get", attribute]]
    for value in categories:
        match += [str(value) if as_text else value, colors[value]]
    match.append(OTHER_COLOR)
    metadata = _legend_meta(title)
    if base == "LineString":
        style = [
            {
                "id": f"{role}-line",
                "type": "line",
                "metadata": metadata,
                "layout": {"line-cap": "round", "line-join": "round"},
                "paint": {"line-color": match, "line-width": 2.5},
            }
        ]
    elif base == "Point":
        style = [
            {
                "id": f"{role}-circle",
                "type": "circle",
                "metadata": metadata,
                "paint": {
                    "circle-radius": 5,
                    "circle-color": match,
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 1,
                },
            }
        ]
    else:
        style = [
            {
                "id": f"{role}-fill",
                "type": "fill",
                "metadata": metadata,
                "paint": {"fill-color": match, "fill-opacity": 0.7},
            },
            {
                "id": f"{role}-outline",
                "type": "line",
                "metadata": metadata,
                "paint": {"line-color": "#ffffff", "line-width": 0.5},
            },
        ]
    value_labels = labels.get(attribute, {})
    items = [
        {"color": colors[value], "value": value, "label": value_labels.get(str(value), str(value))}
        for value in categories
    ]
    if len(counts) > len(categories):
        items.append({"color": OTHER_COLOR, "value": None, "label": "Other"})
    legend = {"type": "categorical", "title": title, "attribute": attribute, "geometry": base, "items": items}
    return StyleResult(style, legend)


def heatmap_style(role: str, title: str, values: AttributeValues) -> StyleResult:
    attribute = values.name
    high = max(values.values) or 1
    ramp: list = ["interpolate", ["linear"], ["heatmap-density"]]
    for index, color in enumerate(HEATMAP_RAMP):
        ramp += [round(index / (len(HEATMAP_RAMP) - 1), 2), color]
    metadata = _legend_meta(title)
    style = [
        {
            "id": f"{role}-heatmap",
            "type": "heatmap",
            "metadata": metadata,
            "maxzoom": 16,
            "paint": {
                "heatmap-weight": [
                    "interpolate", ["linear"], ["to-number", ["get", attribute], 0], 0, 0, high, 1
                ],
                "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 8, 1, 15, 3],
                "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 8, 4, 15, 24],
                "heatmap-color": ramp,
                "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0.85, 16, 0],
            },
        },
        {
            # Individual points fade in where the heatmap fades out.
            "id": f"{role}-circle",
            "type": "circle",
            "metadata": metadata,
            "minzoom": 13,
            "paint": {
                "circle-radius": 4,
                "circle-color": HEATMAP_RAMP[-1],
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 1,
                "circle-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0, 16, 1],
            },
        },
    ]
    legend = {
        "type": "heatmap",
        "title": title,
        "attribute": attribute,
        "geometry": "Point",
        "colors": HEATMAP_RAMP[1:],
        "items": [{"label": "Low", "value": 0}, {"label": "High", "value": high}],
    }
    return StyleResult(style, legend)


def diverging_thresholds(values: list[float]) -> tuple[float, float] | None:
    """Rounded magnitudes ``(small, large)`` splitting changes into five symmetric classes.

    ``small`` is the typical change (median of the non-zero magnitudes) and
    ``large`` the top tenth; changes within ±small read as "about the same".
    """
    magnitudes = sorted(abs(value) for value in values if value != 0)
    if not magnitudes:
        return None
    small = _nice(statistics.median(magnitudes))
    large = _nice(statistics.quantiles(magnitudes, n=10, method="inclusive")[-1]) if len(magnitudes) > 1 else small
    if large <= small:
        large = _nice(small * 2.5)
        if large <= small:
            large = small * 2
    return small, large


def _signed(value: float) -> str:
    return f"+{_format(value)}" if value > 0 else _format(value).replace("-", "−")


def diverging_style(role: str, title: str, base: str, values: AttributeValues) -> StyleResult:
    attribute = values.name
    thresholds = diverging_thresholds(values.values)
    metadata = _legend_meta(title)
    value = ["to-number", ["get", attribute], 0]
    if thresholds is None:
        breaks, colors = [], [DIVERGING[2]]
        items = [{"color": DIVERGING[2], "label": "No change", "min": 0, "max": 0}]
    else:
        small, large = thresholds
        breaks, colors = [-large, -small, small, large], DIVERGING
        items = [
            {"color": DIVERGING[0], "label": f"< {_signed(-large)}", "max": -large},
            {"color": DIVERGING[1], "label": f"{_signed(-large)} – {_signed(-small)}", "min": -large, "max": -small},
            {"color": DIVERGING[2], "label": f"{_signed(-small)} – {_signed(small)}", "min": -small, "max": small},
            {"color": DIVERGING[3], "label": f"{_signed(small)} – {_signed(large)}", "min": small, "max": large},
            {"color": DIVERGING[4], "label": f"≥ {_signed(large)}", "min": large},
        ]
    color = _step(attribute, colors, breaks)
    # Bigger changes draw on top.
    magnitude = ["abs", value]
    if base == "LineString":
        width = _step(attribute, [4.5, 3, 1, 3, 4.5], breaks) if breaks else 1
        style = [
            {
                "id": f"{role}-line",
                "type": "line",
                "metadata": metadata,
                "layout": {"line-cap": "round", "line-join": "round", "line-sort-key": magnitude},
                "paint": {
                    "line-color": color,
                    "line-width": [
                        "interpolate", ["linear"], ["zoom"],
                        8, ["*", 0.5, width],
                        14, ["*", 1.5, width],
                    ],
                },
            }
        ]
    elif base == "Point":
        style = [
            {
                "id": f"{role}-circle",
                "type": "circle",
                "metadata": metadata,
                "layout": {"circle-sort-key": magnitude},
                "paint": {
                    "circle-radius": _step(attribute, [7, 5, 3, 5, 7], breaks) if breaks else 3,
                    "circle-color": color,
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 1,
                },
            }
        ]
    else:
        style = [
            {
                "id": f"{role}-fill",
                "type": "fill",
                "metadata": metadata,
                "paint": {"fill-color": color, "fill-opacity": 0.7},
            },
            {
                "id": f"{role}-outline",
                "type": "line",
                "metadata": metadata,
                "paint": {"line-color": "#ffffff", "line-width": 0.5, "line-opacity": 0.8},
            },
        ]
    legend = {"type": "diverging", "title": title, "attribute": attribute, "geometry": base, "items": items}
    return StyleResult(style, legend)
