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
        self, epsg: int = CoordinateSystem.UTMZ10N, tolerance_pct: float = 0.01
    ) -> None:
        """
        Initialize the tree placement generator.

        Args:
            epsg: EPSG code for UTM projection
            tolerance_pct: Tolerance buffer as percentage of polygon height (default: 1%)
        """
        self.coord_system = CoordinateSystem(epsg)
        self.tolerance_pct = tolerance_pct

    def generate_tree_points(
        self,
        polygon_coords: List[Tuple[float, float]],
        top_edge_start: Tuple[float, float],
        top_edge_end: Tuple[float, float],
        trees_per_row: List[int],
    ) -> List[Dict[str, Any]]:
        """
        Generate tree placement points within a polygon.

        Args:
            polygon_coords: List of (lon, lat) coordinates defining the polygon
            top_edge_start: (lon, lat) coordinates of top edge start point
            top_edge_end: (lon, lat) coordinates of top edge end point
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
            self.coord_system.latlon_to_xy(lat, lon) for lon, lat in polygon_coords
        ]
        poly = Polygon(polygon_xy)

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
        return self._generate_points_in_local_system(
            poly_local, trees_per_row, rotation_info
        )

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
        min_x, min_y, max_x, max_y = poly_local.bounds

        # Calculate effective bounds with tolerance buffer
        height = max_y - min_y
        tolerance = height * self.tolerance_pct

        effective_max_y = max_y - tolerance
        effective_min_y = min_y + tolerance
        effective_height = effective_max_y - effective_min_y

        # Calculate row spacing
        rows = len(trees_per_row)
        row_spacing = effective_height / (rows - 1) if rows > 1 else 0

        tree_points = []
        tree_counter = 1

        for row_index, num_trees in enumerate(trees_per_row):
            y = effective_max_y - row_index * row_spacing

            # Find polygon width at this y level
            segment_coords = self._find_polygon_width_at_y(poly_local, y, min_x, max_x)

            if segment_coords is None:
                continue

            start_x, end_x = segment_coords

            # Generate tree positions for this row
            for col_index in range(num_trees):
                x = self._calculate_tree_x_position(
                    start_x, end_x, col_index, num_trees
                )

                # Transform back to global coordinates
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


# Convenience functions for backward compatibility
def latlon_to_xy(lat: float, lon: float, epsg: int = 32610) -> Tuple[float, float]:
    """
    Convert latitude/longitude to UTM coordinates.

    Args:
        lat: Latitude in decimal degrees
        lon: Longitude in decimal degrees
        epsg: EPSG code for target projection (default: 32610)

    Returns:
        Tuple of (x, y) coordinates in UTM projection
    """
    coord_system = CoordinateSystem(epsg)
    return coord_system.latlon_to_xy(lat, lon)


def xy_to_latlon(x: float, y: float, epsg: int = 32610) -> Tuple[float, float]:
    """
    Convert UTM coordinates to latitude/longitude.

    Args:
        x: X coordinate in UTM projection
        y: Y coordinate in UTM projection
        epsg: EPSG code for source projection (default: 32610)

    Returns:
        Tuple of (lat, lon) in decimal degrees
    """
    coord_system = CoordinateSystem(epsg)
    return coord_system.xy_to_latlon(x, y)


def generate_tree_points(
    polygon_coords: List[Tuple[float, float]],
    top_edge_start: Tuple[float, float],
    top_edge_end: Tuple[float, float],
    trees_per_row: List[int],
) -> List[Dict[str, Any]]:
    """
    Generate tree placement points within a polygon.

    Args:
        polygon_coords: List of (lon, lat) coordinates defining the polygon
        top_edge_start: (lon, lat) coordinates of top edge start point
        top_edge_end: (lon, lat) coordinates of top edge end point
        trees_per_row: List containing number of trees for each row

    Returns:
        List of dictionaries containing tree placement information
    """
    generator = TreePlacementGenerator()
    return generator.generate_tree_points(
        polygon_coords, top_edge_start, top_edge_end, trees_per_row
    )
