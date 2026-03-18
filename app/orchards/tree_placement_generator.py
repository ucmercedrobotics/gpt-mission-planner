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
    """Enum for tree traversal direction."""

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
            traversal_axis: TraversalAxis enum or string ("row" or "column")
            epsg: EPSG code for UTM projection
        """
        self.coord_system = CoordinateSystem(epsg)
        self.polygon_coords = self._make_polygon_array(polygon_coords)
        self.dimensions = self._make_dimension_array(dimensions)
        self.perimeter_margin_m = perimeter_margin_m
        self.traversal_axis = self._parse_traversal_axis(traversal_axis)
        self.perimeter_waypoints: List[Dict[str, Any]] = []

    def _parse_traversal_axis(self, axis: TraversalAxis | str) -> TraversalAxis:
        """Convert string or enum to TraversalAxis enum."""
        if isinstance(axis, TraversalAxis):
            return axis
        axis_str = str(axis).strip().lower()
        if axis_str == TraversalAxis.ROW.value:
            return TraversalAxis.ROW
        if axis_str == TraversalAxis.COLUMN.value:
            return TraversalAxis.COLUMN
        raise ValueError(f"Invalid traversal axis '{axis}'. Must be 'row' or 'column'.")

    def generate_tree_payload(self) -> Dict[str, Any]:
        """Generate compact payload with minimal tree fields and axis-indexed entrances."""
        poly_local, rotation_info = self._build_local_geometry()
        trees = self._generate_minimal_tree_points(
            poly_local, self.dimensions, rotation_info
        )

        axis_waypoints = self._generate_lane_waypoints(
            poly_local, self.dimensions, rotation_info
        )

        self.perimeter_waypoints = axis_waypoints

        aisle_entrances: List[Dict[str, Any]] = []
        aisle_to_entrance_indices: Dict[str, List[int]] = {}

        for waypoint in axis_waypoints:
            lane_index = waypoint.get("lane_index")
            if not isinstance(lane_index, int):
                continue

            lane_entrance_indices: List[int] = []
            for side in ("entry", "exit"):
                point = waypoint.get(side)
                if not isinstance(point, (tuple, list)) or len(point) < 2:
                    continue

                aisle_entrances.append(
                    {
                        "entrance_index": len(aisle_entrances) + 1,
                        "lat": float(point[0]),
                        "lon": float(point[1]),
                    }
                )
                lane_entrance_indices.append(len(aisle_entrances))

            aisle_to_entrance_indices[str(lane_index)] = lane_entrance_indices

        compact_trees = []
        for tree in trees:
            compact_trees.append(
                {
                    "tree_index": tree["tree_index"],
                    "row": tree["row"],
                    "col": tree["col"],
                    "lat": tree["lat"],
                    "lon": tree["lon"],
                    "row_waypoints": tree["row_waypoints"],
                }
            )

        payload: Dict[str, Any] = {
            "traversal_axis": self.traversal_axis.value,
            "trees": compact_trees,
            "aisle_entrances": aisle_entrances,
            "aisle_to_entrance_indices": aisle_to_entrance_indices,
        }

        return payload

    def _build_local_geometry(self) -> Tuple[Polygon, Dict[str, float]]:
        """Build local rotated polygon and transformation context."""
        polygon_xy = [
            self.coord_system.latlon_to_xy(lat, lon) for lon, lat in self.polygon_coords
        ]
        top_edge_start = self.polygon_coords[0]
        top_edge_end = self.polygon_coords[1]

        top_start_xy = self.coord_system.latlon_to_xy(
            top_edge_start[1], top_edge_start[0]
        )
        top_end_xy = self.coord_system.latlon_to_xy(top_edge_end[1], top_edge_end[0])
        rotation_info = self._calculate_rotation(top_start_xy, top_end_xy)
        poly_local = self._transform_polygon_to_local(polygon_xy, rotation_info)
        return poly_local, rotation_info

    def _generate_minimal_tree_points(
        self,
        poly_local: Polygon,
        trees_per_row: List[int],
        rotation_info: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """Generate minimal tree objects with index, row, col, lat, lon."""

        coords = list(poly_local.exterior.coords)
        top_left, top_right, bottom_right, bottom_left = (
            coords[0],
            coords[1],
            coords[2],
            coords[3],
        )

        rows = len(trees_per_row)
        tree_points: List[Dict[str, Any]] = []
        tree_counter = 1
        positions: Dict[Tuple[int, int], Tuple[float, float, int]] = {}

        for row_index, num_trees in enumerate(trees_per_row):
            t = row_index / (rows - 1) if rows > 1 else 0

            row_start_x = (1 - t) * top_left[0] + t * bottom_left[0]
            row_start_y = (1 - t) * top_left[1] + t * bottom_left[1]
            row_end_x = (1 - t) * top_right[0] + t * bottom_right[0]
            row_end_y = (1 - t) * top_right[1] + t * bottom_right[1]

            for col_index in range(num_trees):
                u = col_index / (num_trees - 1) if num_trees > 1 else 0.5
                x = (1 - u) * row_start_x + u * row_end_x
                y = (1 - u) * row_start_y + u * row_end_y

                lat, lon = self._transform_to_global_coords(x, y, rotation_info)
                tree_points.append(
                    {
                        "tree_index": tree_counter,
                        "row": row_index + 1,
                        "col": col_index + 1,
                        "lat": lat,
                        "lon": lon,
                        "row_waypoints": [],
                    }
                )
                positions[(row_index, col_index)] = (
                    x,
                    y,
                    len(tree_points) - 1,
                )
                tree_counter += 1

        for (row_idx, col_idx), (x, y, idx) in positions.items():
            next_col_key = (row_idx, col_idx + 1)
            if next_col_key not in positions:
                continue
            next_x, next_y, next_idx = positions[next_col_key]
            midpoint_x = (x + next_x) / 2
            midpoint_y = (y + next_y) / 2
            midpoint_lat, midpoint_lon = self._transform_to_global_coords(
                midpoint_x, midpoint_y, rotation_info
            )
            midpoint = (midpoint_lat, midpoint_lon)
            tree_points[idx]["row_waypoints"].append(midpoint)
            tree_points[next_idx]["row_waypoints"].append(midpoint)

        return tree_points

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
        """Generate one entry/exit pair for each aisle centerline.

        Aisles are in-between lines:
        - ROW traversal -> aisles between columns
        - COLUMN traversal -> aisles between rows
        """
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
        axis_size = rows if self.traversal_axis == TraversalAxis.ROW else cols
        lane_count = max(0, axis_size - 1)

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
        """Return interpolation edges for aisle centerlines.

        ROW traversal: aisles are between rows, so each aisle line runs
        west/east from left to right.

        COLUMN traversal: aisles are between columns, so each aisle line runs
        north/south from top to bottom.
        """
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
