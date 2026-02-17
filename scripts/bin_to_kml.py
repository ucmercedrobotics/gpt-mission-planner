#!/usr/bin/env python3
"""
Convert length-prefixed .bin payloads (JSON) to a KML file.

The .bin format is a stream of 4-byte big-endian length prefixes followed
by UTF-8 payloads. We scan for JSON payloads containing tree points and
row waypoints, then render them into a KML with distinct colors and a
polygon around all points.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any, Iterable
from xml.etree.ElementTree import Element, SubElement, tostring
import math
from xml.dom.minidom import parseString


def read_length_prefixed_chunks(file_path: Path) -> list[bytes]:
    chunks: list[bytes] = []
    with file_path.open("rb") as f:
        while True:
            header = f.read(4)
            if len(header) < 4:
                break
            length = struct.unpack("!I", header)[0]
            if length <= 0:
                break
            payload = f.read(length)
            if len(payload) < length:
                break
            chunks.append(payload)
    return chunks


def iter_json_payloads_from_bin(file_path: Path) -> Iterable[Any]:
    for chunk in read_length_prefixed_chunks(file_path):
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            data = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        yield data


def iter_json_payloads_from_json_file(file_path: Path) -> Iterable[Any]:
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    yield data


def is_point_dict(item: Any) -> bool:
    return isinstance(item, dict) and ("lat" in item and "lon" in item)


def extract_tree_and_row_waypoints(
    payloads: Iterable[Any],
) -> tuple[list[dict[str, Any]], list[tuple[float, float]]]:
    best_points: list[dict[str, Any]] = []
    row_waypoints: list[tuple[float, float]] = []

    for data in payloads:
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            continue

        points = [item for item in data if is_point_dict(item)]
        if not points:
            continue

        has_row_waypoints = any(
            isinstance(item.get("row_waypoints"), list) for item in points
        )

        if has_row_waypoints:
            best_points = points
            break

        # fallback if no row waypoints found anywhere
        if not best_points:
            best_points = points

    if best_points:
        for item in best_points:
            waypoints = item.get("row_waypoints")
            if not isinstance(waypoints, list):
                continue
            for wp in waypoints:
                if (
                    isinstance(wp, (list, tuple))
                    and len(wp) >= 2
                    and wp[0] is not None
                    and wp[1] is not None
                ):
                    try:
                        row_waypoints.append((float(wp[0]), float(wp[1])))
                    except (TypeError, ValueError):
                        continue

    return best_points, row_waypoints


def apply_offset(
    lat: float, lon: float, north_m: float, east_m: float
) -> tuple[float, float]:
    if not north_m and not east_m:
        return lat, lon
    # Approximate conversion using spherical Earth
    r = 6378137.0
    dlat = north_m / r
    dlon = east_m / (r * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def collect_all_points(
    tree_points: list[dict[str, Any]],
    row_waypoints: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in tree_points:
        try:
            points.append((float(item["lat"]), float(item["lon"])))
        except (TypeError, ValueError, KeyError):
            continue
    points.extend(row_waypoints)
    return points


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    # Monotonic chain for (lat, lon) points
    if len(points) < 3:
        return points

    pts = sorted(set(points))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    return hull


def build_kml(
    tree_points: list[dict[str, Any]],
    row_waypoints: list[tuple[float, float]],
    polygon_points: list[tuple[float, float]],
    offset_north_m: float,
    offset_east_m: float,
) -> str:
    kml = Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = SubElement(kml, "Document")

    # Styles
    tree_style = SubElement(document, "Style", id="treeStyle")
    tree_icon_style = SubElement(tree_style, "IconStyle")
    tree_icon = SubElement(tree_icon_style, "Icon")
    SubElement(tree_icon, "href").text = (
        "http://maps.google.com/mapfiles/kml/shapes/target.png"
    )
    SubElement(tree_icon_style, "color").text = "ff0000ff"
    SubElement(tree_icon_style, "scale").text = "1.1"

    row_style = SubElement(document, "Style", id="rowStyle")
    row_icon_style = SubElement(row_style, "IconStyle")
    row_icon = SubElement(row_icon_style, "Icon")
    SubElement(row_icon, "href").text = (
        "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
    )
    SubElement(row_icon_style, "color").text = "ff00ff00"
    SubElement(row_icon_style, "scale").text = "1.0"

    poly_style = SubElement(document, "Style", id="boundaryStyle")
    line = SubElement(poly_style, "LineStyle")
    SubElement(line, "color").text = "ffff0000"
    SubElement(line, "width").text = "2"
    poly = SubElement(poly_style, "PolyStyle")
    SubElement(poly, "color").text = "330000ff"

    # Tree points
    for idx, item in enumerate(tree_points, start=1):
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        lat, lon = apply_offset(lat, lon, offset_north_m, offset_east_m)
        placemark = SubElement(document, "Placemark")
        name = SubElement(placemark, "name")
        name.text = f"Tree {idx}"
        SubElement(placemark, "styleUrl").text = "#treeStyle"
        point = SubElement(placemark, "Point")
        SubElement(point, "coordinates").text = f"{lon},{lat},0"

    # Row waypoints
    for idx, (lat, lon) in enumerate(row_waypoints, start=1):
        lat, lon = apply_offset(lat, lon, offset_north_m, offset_east_m)
        placemark = SubElement(document, "Placemark")
        name = SubElement(placemark, "name")
        name.text = f"Row Waypoint {idx}"
        SubElement(placemark, "styleUrl").text = "#rowStyle"
        point = SubElement(placemark, "Point")
        SubElement(point, "coordinates").text = f"{lon},{lat},0"

    # Boundary polygon
    if len(polygon_points) >= 3:
        placemark = SubElement(document, "Placemark")
        SubElement(placemark, "name").text = "Boundary"
        SubElement(placemark, "styleUrl").text = "#boundaryStyle"
        polygon = SubElement(placemark, "Polygon")
        SubElement(polygon, "tessellate").text = "1"
        outer = SubElement(polygon, "outerBoundaryIs")
        ring = SubElement(outer, "LinearRing")
        coords = SubElement(ring, "coordinates")
        coord_lines = []
        for lat, lon in polygon_points:
            lat, lon = apply_offset(lat, lon, offset_north_m, offset_east_m)
            coord_lines.append(f"{lon},{lat},0")
        # close ring
        first_lat, first_lon = apply_offset(
            polygon_points[0][0],
            polygon_points[0][1],
            offset_north_m,
            offset_east_m,
        )
        coord_lines.append(f"{first_lon},{first_lat},0")
        coords.text = "\n" + "\n".join(coord_lines) + "\n"

    rough = tostring(kml, "utf-8")
    return parseString(rough).toprettyxml(indent="  ")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert .bin (length-prefixed JSON) to KML with tree/row waypoints."
    )
    parser.add_argument("input", help="Path to .bin or .json file")
    parser.add_argument("output", help="Path to output .kml file")
    parser.add_argument(
        "--offset-north-m",
        type=float,
        default=0.0,
        help="Offset all points northward in meters",
    )
    parser.add_argument(
        "--offset-east-m",
        type=float,
        default=0.0,
        help="Offset all points eastward in meters",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    if input_path.suffix.lower() == ".json":
        payloads = list(iter_json_payloads_from_json_file(input_path))
    else:
        payloads = list(iter_json_payloads_from_bin(input_path))

    tree_points, row_waypoints = extract_tree_and_row_waypoints(payloads)
    if not tree_points and not row_waypoints:
        raise SystemExit("No tree points or row waypoints found in input.")

    all_points = collect_all_points(tree_points, row_waypoints)
    hull = convex_hull(all_points)
    kml_content = build_kml(
        tree_points,
        row_waypoints,
        hull,
        args.offset_north_m,
        args.offset_east_m,
    )

    output_path = Path(args.output)
    output_path.write_text(kml_content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
