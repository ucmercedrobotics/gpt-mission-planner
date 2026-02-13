"""
Tree placement coordinate system with coordinate transformations.

This module provides functionality to place trees within a polygon area
using coordinate transformations between WGS84 and UTM projections.
"""

from typing import Tuple

from pyproj import Transformer


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
