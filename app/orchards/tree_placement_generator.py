"""
Tree placement coordinate system with coordinate transformations.

This module provides functionality to place trees within a polygon area
using coordinate transformations between WGS84 and UTM projections.
"""

import math
from enum import Enum
from typing import List, Tuple, Dict, Any

import numpy as np
from shapely.geometry import Polygon

from app.utils.gps_utils import CoordinateSystem


class TraversalAxis(str, Enum):
    ROW = "row"
    COLUMN = "column"


class TreePlacementGenerator:
    """Generates tree placement points within a polygon area."""

    TRAVERSAL_ROW = TraversalAxis.ROW
    TRAVERSAL_COLUMN = TraversalAxis.COLUMN

    def __init__(
        self,
        polygon_coords: list,
        dimensions: list,
        epsg: int = CoordinateSystem.UTMZ10N,
        perimeter_margin_m: float = 5.0,
        traversal_axis: TraversalAxis | str = TraversalAxis.COLUMN,
    ) -> None:
        """
        Initialize the tree placement generator.

        Args:
            epsg: EPSG code for UTM projection
        """
        self.coord_system = CoordinateSystem(epsg)
        self.polygon_coords = self._make_polygon_array(polygon_coords)
        self.dimensions = self._make_dimension_array(dimensions)
        self.perimeter_margin_m = perimeter_margin_m
        self.traversal_axis: TraversalAxis = self._validate_traversal_axis(
            traversal_axis
        )
        self.perimeter_waypoints: List[Dict[str, Any]] = []

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
            - row_waypoints: List of (lat, lon) tuples for waypoints between trees in the same row
            - perimeter_waypoints: Lane-based entry/exit waypoints outside block edges
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

        # Build lane-based perimeter waypoints (one per in-between path), each with entry+exit.
        self.perimeter_waypoints = self._generate_lane_waypoints(
            poly_local, self.dimensions, rotation_info
        )

        # Generate tree points
        self.tree_points = self._generate_points_in_local_system(
            poly_local,
            self.dimensions,
            rotation_info,
            self.perimeter_waypoints,
        )
        return self.tree_points

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
        perimeter_waypoints: List[Dict[str, Any]],
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
        cols = int(max(trees_per_row)) if rows > 0 else 0
        tree_points = []
        tree_counter = 1
        positions = {}

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
                tree_lane_waypoints = self._get_tree_lane_waypoints(
                    row_index=row_index,
                    col_index=col_index,
                    lane_waypoints=perimeter_waypoints,
                    rows=rows,
                    cols=cols,
                )

                tree_points.append(
                    {
                        "tree_index": tree_counter,
                        "row": row_index + 1,
                        "col": col_index + 1,
                        "lat": lat,
                        "lon": lon,
                        "row_waypoints": [],
                        "perimeter_waypoints": tree_lane_waypoints,
                    }
                )
                positions[(row_index, col_index)] = (x, y, tree_counter - 1)
                tree_counter += 1

        # adding waypoints in between trees
        for (row_idx, col_idx), (x, y, idx) in positions.items():
            next_col_key = (row_idx, col_idx + 1)
            if next_col_key in positions:
                nx, ny, nidx = positions[next_col_key]
                mx = (x + nx) / 2
                my = (y + ny) / 2
                mlat, mlon = self._transform_to_global_coords(mx, my, rotation_info)
                tree_points[idx]["row_waypoints"].append((mlat, mlon))
                tree_points[nidx]["row_waypoints"].append((mlat, mlon))

        return tree_points

    def _get_tree_lane_waypoints(
        self,
        row_index: int,
        col_index: int,
        lane_waypoints: List[Dict[str, Any]],
        rows: int,
        cols: int,
    ) -> List[Dict[str, Any]]:
        """Return only the entry/exit lane waypoint records adjacent to a tree."""
        if not lane_waypoints:
            return []

        axis_size = self._select_by_axis(rows, cols)
        lane_count = self._lane_count(axis_size)
        primary_index = self._select_by_axis(row_index, col_index)
        candidate_lane_indices = {
            lane_idx
            for lane_idx in (primary_index, primary_index + 1)
            if 1 <= lane_idx <= lane_count
        }

        if not candidate_lane_indices:
            return []

        return [
            lane
            for lane in lane_waypoints
            if lane.get("lane_index") in candidate_lane_indices
        ]

    def _validate_traversal_axis(
        self, traversal_axis: TraversalAxis | str
    ) -> TraversalAxis:
        """Validate traversal axis value."""
        if isinstance(traversal_axis, TraversalAxis):
            return traversal_axis

        axis = (traversal_axis or "").strip().lower()
        if axis == TraversalAxis.ROW.value:
            return TraversalAxis.ROW
        if axis == TraversalAxis.COLUMN.value:
            return TraversalAxis.COLUMN

        raise ValueError(
            "traversal_axis must be TreePlacementGenerator.TRAVERSAL_ROW or "
            "TreePlacementGenerator.TRAVERSAL_COLUMN."
        )

    def _interpolate_point(
        self,
        p0: Tuple[float, float],
        p1: Tuple[float, float],
        t: float,
    ) -> Tuple[float, float]:
        """Linear interpolation between two points."""
        return ((1 - t) * p0[0] + t * p1[0], (1 - t) * p0[1] + t * p1[1])

    def _extend_segment_ends(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Extend segment both directions by perimeter margin."""
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length == 0:
            return start, end
        ux = dx / length
        uy = dy / length
        entry = (
            start[0] - self.perimeter_margin_m * ux,
            start[1] - self.perimeter_margin_m * uy,
        )
        exit = (
            end[0] + self.perimeter_margin_m * ux,
            end[1] + self.perimeter_margin_m * uy,
        )
        return entry, exit

    def _generate_lane_waypoints(
        self,
        poly_local: Polygon,
        trees_per_row: List[int],
        rotation_info: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """Generate one entry/exit waypoint pair for each in-between row/column lane."""
        coords = list(poly_local.exterior.coords)
        top_left, top_right, bottom_right, bottom_left = (
            coords[0],
            coords[1],
            coords[2],
            coords[3],
        )

        lanes: List[Dict[str, Any]] = []
        rows = len(trees_per_row)
        cols = int(max(trees_per_row)) if len(trees_per_row) > 0 else 0
        axis_size = self._select_by_axis(rows, cols)
        lane_count = self._lane_count(axis_size)

        if lane_count <= 0:
            return lanes

        start_edge, end_edge = self._axis_edges(
            top_left,
            top_right,
            bottom_right,
            bottom_left,
        )

        for lane_idx in range(lane_count):
            t_mid = (lane_idx + 0.5) / lane_count
            lane_start = self._interpolate_point(start_edge[0], start_edge[1], t_mid)
            lane_end = self._interpolate_point(end_edge[0], end_edge[1], t_mid)
            entry_local, exit_local = self._extend_segment_ends(lane_start, lane_end)

            entry_latlon = self._transform_to_global_coords(
                entry_local[0], entry_local[1], rotation_info
            )
            exit_latlon = self._transform_to_global_coords(
                exit_local[0], exit_local[1], rotation_info
            )
            lanes.append(
                {
                    "axis": self.traversal_axis.value,
                    "lane_index": lane_idx + 1,
                    "entry": entry_latlon,
                    "exit": exit_latlon,
                }
            )

        return lanes

    def _select_by_axis(self, row_value: int, col_value: int) -> int:
        """Select row or column value according to traversal axis."""
        return row_value if self.traversal_axis == TraversalAxis.ROW else col_value

    def _lane_count(self, axis_size: int) -> int:
        """Number of in-between lanes from a single axis size."""
        return max(0, axis_size - 1)

    def _axis_edges(
        self,
        top_left: Tuple[float, float],
        top_right: Tuple[float, float],
        bottom_right: Tuple[float, float],
        bottom_left: Tuple[float, float],
    ) -> Tuple[
        Tuple[Tuple[float, float], Tuple[float, float]],
        Tuple[Tuple[float, float], Tuple[float, float]],
    ]:
        """Return start/end edges to interpolate one lane centerline generically."""
        if self.traversal_axis == TraversalAxis.ROW:
            return (top_left, bottom_left), (top_right, bottom_right)
        return (top_left, top_right), (bottom_left, bottom_right)

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
