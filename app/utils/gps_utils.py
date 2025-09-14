"""
Tree placement coordinate system with coordinate transformations.

This module provides functionality to place trees within a polygon area
using coordinate transformations between WGS84 and UTM projections.
"""

import math
from typing import List, Tuple, Dict, Any, Optional

import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, LineString
from lxml import etree

from .xml_utils import NS


class CoordinateSystem:
    """Handles coordinate transformations between different EPSG systems."""

    # Class constants
    WGS84: int = 4326
    UTMZ10N: int = 32610
    EPSG_PREFIX: str = "EPSG:"

    def __init__(self, target_epsg: int = UTMZ10N) -> None:
        """
        Initialize the coordinate system.

        Args:
            target_epsg: Target EPSG code for UTM projection (default: 32610 for UTM Zone 10N)
        """
        self.target_epsg = target_epsg
        self._to_utm_transformer = Transformer.from_crs(
            f"{self.EPSG_PREFIX}{self.WGS84}",
            f"{self.EPSG_PREFIX}{target_epsg}",
            always_xy=True,
        )
        self._from_utm_transformer = Transformer.from_crs(
            f"{self.EPSG_PREFIX}{target_epsg}",
            f"{self.EPSG_PREFIX}{self.WGS84}",
            always_xy=True,
        )

    def latlon_to_xy(self, lat: float, lon: float) -> Tuple[float, float]:
        """
        Convert latitude/longitude to UTM coordinates.

        Args:
            lat: Latitude in decimal degrees
            lon: Longitude in decimal degrees

        Returns:
            Tuple of (x, y) coordinates in UTM projection
        """
        return self._to_utm_transformer.transform(lon, lat)

    def xy_to_latlon(self, x: float, y: float) -> Tuple[float, float]:
        """
        Convert UTM coordinates to latitude/longitude.

        Args:
            x: X coordinate in UTM projection
            y: Y coordinate in UTM projection

        Returns:
            Tuple of (lat, lon) in decimal degrees
        """
        lon, lat = self._from_utm_transformer.transform(x, y)
        return lat, lon


class TreePlacementGenerator:
    """Generates tree placement points within a polygon area."""

    def __init__(
        self,
        polygon_coords: list,
        dimensions: list,
        epsg: int = CoordinateSystem.UTMZ10N,
        tolerance_pct: float = 0.001,
    ) -> None:
        """
        Initialize the tree placement generator.

        Args:
            epsg: EPSG code for UTM projection
            tolerance_pct: Tolerance buffer as percentage of polygon height (default: 1%)
        """
        self.coord_system = CoordinateSystem(epsg)
        self.tolerance_pct = tolerance_pct
        self.polygon_coords = self._make_polygon_array(polygon_coords)
        self.dimensions = self._make_dimension_array(dimensions)

    def generate_tree_points(
        self,
    ) -> List[Dict[str, Any]]:
        """
        Generate tree placement points within a polygon.

        Args:
            polygon_coords: List of (lon, lat) coordinates defining the polygon. We assume index 0 -> 1 edge is north facing!!
            trees_per_row: List containing number of trees for each row

        Returns:
            List of dictionaries containing tree placement information with keys:
            - tree_index: Sequential tree number
            - row: Row number (1-based)
            - col: Column number within row (1-based)
            - lat: Latitude in decimal degrees
            - lon: Longitude in decimal degrees
        """
        # Convert to UTM and create polygon
        polygon_xy = [
            self.coord_system.latlon_to_xy(lat, lon) for lon, lat in self.polygon_coords
        ]
        top_edge_start = self.polygon_coords[0]
        top_edge_end = self.polygon_coords[1]

        # Get top edge coordinates in UTM
        top_start_xy = self.coord_system.latlon_to_xy(
            top_edge_start[1], top_edge_start[0]
        )
        top_end_xy = self.coord_system.latlon_to_xy(top_edge_end[1], top_edge_end[0])

        # Calculate rotation to make top edge horizontal
        rotation_info = self._calculate_rotation(top_start_xy, top_end_xy)

        # Transform polygon to local coordinate system
        poly_local = self._transform_polygon_to_local(polygon_xy, rotation_info)

        # Generate tree points
        self.tree_points = self._generate_points_in_local_system(
            poly_local, self.dimensions, rotation_info
        )
        return self.tree_points

    def replace_tree_ids_with_gps(self, xml_file: str) -> str:
        """Replace tree IDs in the XML file with their GPS coordinates.

        Args:
            xml_file (str): Path to the XML file to modify.

        Returns:
            str: Path to the modified XML file.
        """
        # Load the XML string
        root = etree.parse(xml_file).getroot()

        # Find all tree elements and replace their IDs with GPS coordinates
        for tree_elem in root.xpath(".//task:moveToGPSLocation", namespaces=NS):
            id_elem = tree_elem.find(".//task:id", namespaces=NS)
            # we assume the LLM filled in the GPS, possibly on edge of polygon.
            if id_elem is None:
                continue
            id = id_elem.text
            gps_coords = self.tree_points[int(id) - 1]
            if gps_coords:
                # Store the original tail whitespace before removing
                tail_whitespace = id_elem.tail
                # Remove the id element first
                tree_elem.remove(id_elem)
                # Create latitude element with proper indentation
                lat_elem = etree.SubElement(
                    tree_elem, etree.QName(NS["task"], "latitude")
                )
                lat_elem.text = str(gps_coords["lat"])
                lat_elem.tail = tail_whitespace  # Preserve indentation
                # Create longitude element with proper indentation
                lon_elem = etree.SubElement(
                    tree_elem, etree.QName(NS["task"], "longitude")
                )
                lon_elem.text = str(gps_coords["lon"])
                lon_elem.tail = tail_whitespace  # Preserve indentation

        # Write the modified XML back to the original file
        # Ensure proper formatting by re-parsing and indenting
        etree.indent(root, space="    ")  # 4 spaces indentation
        with open(xml_file, "w", encoding="utf-8") as f:
            f.write(etree.tostring(root, pretty_print=True, encoding="unicode"))

        return xml_file

    def _make_polygon_array(self, coords: list) -> np.ndarray:
        """Create a 2D array representing the polygon coordinates."""
        coords_array = []
        for p in coords:
            if len(p) != 2:
                raise ValueError("Each coordinate must be a tuple of (lon, lat).")
            lon = p["lon"]
            lat = p["lat"]
            # Create a 2D array for each coordinate
            coords_array.append([lon, lat])
        return np.array(coords_array, dtype=np.float64)

    def _make_dimension_array(self, dimensions: list) -> np.ndarray:
        """Create a 2D array representing the dimensions of the planting area."""
        shape = []
        for d in dimensions:
            if len(d) != 2 or "row" not in d or "col" not in d:
                raise ValueError(
                    "Each dimension must be a dictionary with 'row' and 'col' keys."
                )
            col = d["col"]
            row = d["row"]
            # Create a 2D array for each dimension
            shape += [col] * row
        return np.array(shape, dtype=np.uint8)

    def _calculate_rotation(
        self, start_point: Tuple[float, float], end_point: Tuple[float, float]
    ) -> Dict[str, float]:
        """Calculate rotation parameters to align top edge horizontally."""
        dx = end_point[0] - start_point[0]
        dy = end_point[1] - start_point[1]
        theta = math.atan2(dy, dx)

        return {
            "cos_a": np.cos(-theta),
            "sin_a": np.sin(-theta),
            "origin_x": start_point[0],
            "origin_y": start_point[1],
        }

    def _transform_polygon_to_local(
        self, polygon_xy: List[Tuple[float, float]], rotation_info: Dict[str, float]
    ) -> Polygon:
        """Transform polygon coordinates to local rotated coordinate system."""
        cos_a = rotation_info["cos_a"]
        sin_a = rotation_info["sin_a"]
        origin_x = rotation_info["origin_x"]
        origin_y = rotation_info["origin_y"]

        poly_local_coords = []
        for x, y in polygon_xy:
            # Translate to origin
            x_shifted = x - origin_x
            y_shifted = y - origin_y

            # Rotate
            x_rot = cos_a * x_shifted - sin_a * y_shifted
            y_rot = sin_a * x_shifted + cos_a * y_shifted

            poly_local_coords.append((x_rot, y_rot))

        return Polygon(poly_local_coords)

    def _generate_points_in_local_system(
        self,
        poly_local: Polygon,
        trees_per_row: List[int],
        rotation_info: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """Generate tree points within the local coordinate system."""

        # Get polygon boundary coords (assumes 4-point polygon for orchard block)
        coords = list(poly_local.exterior.coords)
        # Order: top-left, top-right, bottom-right, bottom-left
        top_left, top_right, bottom_right, bottom_left = (
            coords[0],
            coords[1],
            coords[2],
            coords[3],
        )

        rows = len(trees_per_row)
        tree_points = []
        tree_counter = 1

        for row_index, num_trees in enumerate(trees_per_row):
            t = row_index / (rows - 1) if rows > 1 else 0  # interpolation factor

            # Interpolate row start and end along polygon edges
            row_start_x = (1 - t) * top_left[0] + t * bottom_left[0]
            row_start_y = (1 - t) * top_left[1] + t * bottom_left[1]
            row_end_x = (1 - t) * top_right[0] + t * bottom_right[0]
            row_end_y = (1 - t) * top_right[1] + t * bottom_right[1]

            for col_index in range(num_trees):
                u = (
                    col_index / (num_trees - 1) if num_trees > 1 else 0.5
                )  # interpolation factor across row
                x = (1 - u) * row_start_x + u * row_end_x
                y = (1 - u) * row_start_y + u * row_end_y

                # Transform back to global coords
                lat, lon = self._transform_to_global_coords(x, y, rotation_info)

                tree_points.append(
                    {
                        "tree_index": tree_counter,
                        "row": row_index + 1,
                        "col": col_index + 1,
                        "lat": lat,
                        "lon": lon,
                    }
                )
                tree_counter += 1

        return tree_points

    def _find_polygon_width_at_y(
        self, poly_local: Polygon, y: float, min_x: float, max_x: float
    ) -> Optional[Tuple[float, float]]:
        """Find the polygon width at a given y level."""
        # Create horizontal scan line
        scan_line = LineString([(min_x - 100, y), (max_x + 100, y)])
        intersection = poly_local.intersection(scan_line)

        if intersection.is_empty:
            return None

        # Handle multiple segments by selecting the longest
        if intersection.geom_type == "MultiLineString":
            segment = max(intersection.geoms, key=lambda s: s.length)
        else:
            segment = intersection

        coords = list(segment.coords)
        start_x, end_x = coords[0][0], coords[-1][0]

        # Ensure start_x <= end_x
        if start_x > end_x:
            start_x, end_x = end_x, start_x

        return start_x, end_x

    def _calculate_tree_x_position(
        self, start_x: float, end_x: float, col_index: int, num_trees: int
    ) -> float:
        """Calculate x position for a tree within a row."""
        if num_trees == 1:
            return (start_x + end_x) / 2
        else:
            return start_x + col_index / (num_trees - 1) * (end_x - start_x)

    def _transform_to_global_coords(
        self, x: float, y: float, rotation_info: Dict[str, float]
    ) -> Tuple[float, float]:
        """Transform local coordinates back to global lat/lon."""
        cos_a = rotation_info["cos_a"]
        sin_a = rotation_info["sin_a"]
        origin_x = rotation_info["origin_x"]
        origin_y = rotation_info["origin_y"]

        # Reverse rotation
        x_global = cos_a * x + sin_a * y + origin_x
        y_global = -sin_a * x + cos_a * y + origin_y

        # Convert back to lat/lon
        return self.coord_system.xy_to_latlon(x_global, y_global)
