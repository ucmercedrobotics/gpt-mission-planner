#!/usr/bin/env python3
"""
XML to KML Converter for Mission Planning

This script parses XML task files containing GPS coordinates and generates
KML files for visualization in Google Earth.
"""

import xml.etree.ElementTree as ET
import argparse
import os


def parse_xml_mission(xml_file_path):
    """
    Parse the XML mission file and extract GPS coordinates.

    Args:
        xml_file_path (str): Path to the XML file

    Returns:
        dict: Contains mission info and list of waypoints
    """
    # Parse the XML file
    tree = ET.parse(xml_file_path)
    root = tree.getroot()

    # Define namespace
    namespace = {"task": "https://robotics.ucmerced.edu/task"}

    # Extract mission information
    task_info = root.find("task:CompositeTaskInformation", namespace)
    task_id = (
        task_info.find("task:TaskID", namespace).text
        if task_info
        else "Unknown Mission"
    )
    task_description = (
        task_info.find("task:TaskDescription", namespace).text
        if task_info
        else "No description"
    )

    # Extract waypoints from atomic tasks
    waypoints = []
    atomic_tasks = root.find("task:AtomicTasks", namespace)

    if atomic_tasks:
        for atomic_task in atomic_tasks.findall("task:AtomicTask", namespace):
            task_id_elem = atomic_task.find("task:TaskID", namespace)
            task_desc_elem = atomic_task.find("task:TaskDescription", namespace)
            action = atomic_task.find("task:Action", namespace)

            if action:
                action_type = action.find("task:ActionType", namespace)
                if action_type is not None and action_type.text == "moveToGPSLocation":
                    gps_location = action.find("task:moveToGPSLocation", namespace)
                    if gps_location:
                        lat_elem = gps_location.find("task:latitude", namespace)
                        lon_elem = gps_location.find("task:longitude", namespace)

                        if lat_elem is not None and lon_elem is not None:
                            waypoint = {
                                "task_id": (
                                    task_id_elem.text
                                    if task_id_elem is not None
                                    else "Unknown"
                                ),
                                "description": (
                                    task_desc_elem.text
                                    if task_desc_elem is not None
                                    else "No description"
                                ),
                                "latitude": float(lat_elem.text),
                                "longitude": float(lon_elem.text),
                            }
                            waypoints.append(waypoint)

    # Extract action sequence for path ordering and conditional logic
    sequence_order = []
    conditional_tasks = {}  # Maps conditional group ID to list of task IDs
    conditional_conditions = {}  # Maps conditional group ID to condition description
    conditional_counter = 0

    def parse_sequence_element(element, parent_conditional_id=None):
        nonlocal conditional_counter

        for child in element:
            if child.tag.endswith("TaskID"):
                task_id = child.text
                sequence_order.append(task_id)
                if parent_conditional_id is not None:
                    if parent_conditional_id not in conditional_tasks:
                        conditional_tasks[parent_conditional_id] = []
                    conditional_tasks[parent_conditional_id].append(task_id)

            elif child.tag.endswith("ConditionalActions"):
                # Extract condition information
                conditional_counter += 1
                current_conditional_id = f"conditional_{conditional_counter}"

                # Parse the condition
                conditional_elem = child.find("task:Conditional", namespace)
                condition_desc = "Unknown condition"
                if conditional_elem is not None:
                    comparator_elem = conditional_elem.find(
                        "task:Comparator", namespace
                    )
                    hard_value_elem = conditional_elem.find("task:HardValue", namespace)

                    if comparator_elem is not None and hard_value_elem is not None:
                        comparator = comparator_elem.text
                        value = hard_value_elem.text

                        # Convert comparator to readable format
                        comparator_map = {
                            "lt": "less than",
                            "gt": "greater than",
                            "eq": "equal to",
                            "le": "less than or equal to",
                            "ge": "greater than or equal to",
                            "ne": "not equal to",
                        }
                        readable_comparator = comparator_map.get(comparator, comparator)
                        condition_desc = f"if value is {readable_comparator} {value}"

                conditional_conditions[current_conditional_id] = condition_desc

                # Parse the conditional sequence
                conditional_sequence = child.find("task:Sequence", namespace)
                if conditional_sequence is not None:
                    parse_sequence_element(conditional_sequence, current_conditional_id)

            elif child.tag.endswith("Sequence"):
                parse_sequence_element(child, parent_conditional_id)

    action_sequence = root.find("task:ActionSequence", namespace)
    if action_sequence:
        sequence = action_sequence.find("task:Sequence", namespace)
        if sequence:
            parse_sequence_element(sequence)

    return {
        "mission_id": task_id,
        "mission_description": task_description,
        "waypoints": waypoints,
        "sequence_order": sequence_order,
        "conditional_tasks": conditional_tasks,
        "conditional_conditions": conditional_conditions,
    }


def create_kml(mission_data, output_file):
    """
    Create a KML file from the mission data.

    Args:
        mission_data (dict): Mission data containing waypoints
        output_file (str): Output KML file path
    """
    # Define colors for conditional task groups (cycling through different colors)
    conditional_colors = [
        "ff0088ff",  # Orange
        "ff00ffff",  # Yellow
        "ffff0088",  # Purple
        "ff88ff00",  # Cyan
        "ffff8800",  # Light Blue
        "ff8800ff",  # Magenta
    ]

    # Create mapping of task IDs to their conditional group (if any)
    task_to_conditional = {}
    conditional_group_colors = {}

    for group_id, task_list in mission_data.get("conditional_tasks", {}).items():
        color_index = len(conditional_group_colors) % len(conditional_colors)
        conditional_group_colors[group_id] = conditional_colors[color_index]
        for task_id in task_list:
            task_to_conditional[task_id] = group_id

    kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{mission_data['mission_id']}</name>
    <description>{mission_data['mission_description']}</description>

    <!-- Styles for different elements -->
    <Style id="waypointStyle">
      <IconStyle>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/shapes/target.png</href>
          <scale>1.2</scale>
        </Icon>
        <color>ff0000ff</color>
      </IconStyle>
      <LabelStyle>
        <scale>1.0</scale>
      </LabelStyle>
    </Style>

    <Style id="pathStyle">
      <LineStyle>
        <color>ff0000ff</color>
        <width>3</width>
      </LineStyle>
    </Style>
"""

    # Add styles for conditional waypoints
    for group_id, color in conditional_group_colors.items():
        kml_content += f"""    <Style id="conditionalStyle_{group_id}">
      <IconStyle>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/shapes/question-mark.png</href>
          <scale>1.3</scale>
        </Icon>
        <color>{color}</color>
      </IconStyle>
      <LabelStyle>
        <scale>1.1</scale>
        <color>{color}</color>
      </LabelStyle>
    </Style>
"""

    # Add styles for conditional paths
    for group_id, color in conditional_group_colors.items():
        kml_content += f"""    <Style id="conditionalPathStyle_{group_id}">
      <LineStyle>
        <color>{color}</color>
        <width>2</width>
        <gx:labelVisibility>1</gx:labelVisibility>
      </LineStyle>
    </Style>
"""

    kml_content += """
    <!-- Waypoint Placemarks -->
"""

    # Add waypoints as placemarks
    waypoint_coords = []
    conditional_waypoints = {}  # Group conditional waypoints by group ID

    for i, waypoint in enumerate(mission_data["waypoints"]):
        # Check if this waypoint is part of a conditional task
        conditional_group = task_to_conditional.get(waypoint["task_id"])

        if conditional_group:
            # This is a conditional waypoint
            condition_desc = mission_data.get("conditional_conditions", {}).get(
                conditional_group, "Unknown condition"
            )
            style_url = f"#conditionalStyle_{conditional_group}"
            description = (
                f"{waypoint['description']}\n\nConditional Task: {condition_desc}"
            )

            # Store for conditional path creation
            if conditional_group not in conditional_waypoints:
                conditional_waypoints[conditional_group] = []
            conditional_waypoints[conditional_group].append(waypoint)
        else:
            # Regular waypoint
            style_url = "#waypointStyle"
            description = waypoint["description"]

        kml_content += f"""    <Placemark>
      <name>{waypoint['task_id']}</name>
      <description>{description}</description>
      <styleUrl>{style_url}</styleUrl>
      <Point>
        <coordinates>{waypoint['longitude']},{waypoint['latitude']},0</coordinates>
      </Point>
    </Placemark>
"""
        waypoint_coords.append((waypoint["longitude"], waypoint["latitude"]))

    # Add main mission path (connecting non-conditional waypoints in sequence order)
    regular_sequence = []
    if mission_data["sequence_order"]:
        waypoint_map = {wp["task_id"]: wp for wp in mission_data["waypoints"]}

        for task_id in mission_data["sequence_order"]:
            if task_id in waypoint_map and task_id not in task_to_conditional:
                regular_sequence.append(waypoint_map[task_id])

    if len(regular_sequence) > 1:
        kml_content += f"""
    <!-- Main Mission Path -->
    <Placemark>
      <name>Main Mission Path</name>
      <description>Path connecting main waypoints in mission sequence</description>
      <styleUrl>#pathStyle</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
"""

        for wp in regular_sequence:
            kml_content += f'          {wp["longitude"]},{wp["latitude"]},0\n'

        kml_content += """        </coordinates>
      </LineString>
    </Placemark>
"""

    # Add conditional paths
    for group_id, waypoints_in_group in conditional_waypoints.items():
        if len(waypoints_in_group) > 0:
            condition_desc = mission_data.get("conditional_conditions", {}).get(
                group_id, "Unknown condition"
            )

            kml_content += f"""
    <!-- Conditional Path: {group_id} -->
    <Placemark>
      <name>Conditional Path ({condition_desc})</name>
      <description>Conditional tasks executed {condition_desc}</description>
      <styleUrl>#conditionalPathStyle_{group_id}</styleUrl>
      <LineString>
        <tessellate>1</tessellate>
        <coordinates>
"""

            for wp in waypoints_in_group:
                kml_content += f'          {wp["longitude"]},{wp["latitude"]},0\n'

            kml_content += """        </coordinates>
      </LineString>
    </Placemark>
"""

    # Add complete mission path (all waypoints in order) as a folder
    if len(waypoint_coords) > 1 and mission_data["sequence_order"]:
        kml_content += f"""
    <Folder>
      <name>Complete Mission Sequence</name>
      <description>All tasks including conditional ones in execution order</description>
      <Placemark>
        <name>Complete Mission Path</name>
        <description>Complete path showing all waypoints in sequence (conditional tasks shown with dashed line)</description>
        <Style>
          <LineStyle>
            <color>80808080</color>
            <width>1</width>
          </LineStyle>
        </Style>
        <LineString>
          <tessellate>1</tessellate>
          <coordinates>
"""

        # Order coordinates according to complete sequence
        waypoint_map = {wp["task_id"]: wp for wp in mission_data["waypoints"]}

        for task_id in mission_data["sequence_order"]:
            if task_id in waypoint_map:
                wp = waypoint_map[task_id]
                kml_content += f'            {wp["longitude"]},{wp["latitude"]},0\n'

        kml_content += """          </coordinates>
        </LineString>
      </Placemark>
    </Folder>
"""

    # Add bounding polygon if we have 4 corner points
    if len(waypoint_coords) == 4:
        kml_content += f"""
    <!-- Farm Boundary -->
    <Placemark>
      <name>Farm Boundary</name>
      <description>Rectangular boundary of the farm</description>
      <Style>
        <LineStyle>
          <color>ff00ff00</color>
          <width>2</width>
        </LineStyle>
        <PolyStyle>
          <color>3300ff00</color>
        </PolyStyle>
      </Style>
      <Polygon>
        <tessellate>1</tessellate>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
"""

        # Sort waypoints to form a proper rectangle (assuming corners)
        # Find corners: NW, NE, SE, SW
        waypoints_with_coords = [
            (wp["latitude"], wp["longitude"], wp) for wp in mission_data["waypoints"]
        ]
        waypoints_with_coords.sort(key=lambda x: (x[0], x[1]))  # Sort by lat, then lon

        # Group into north (higher lat) and south (lower lat)
        mid_lat = (waypoints_with_coords[0][0] + waypoints_with_coords[-1][0]) / 2
        north_points = [wp for wp in waypoints_with_coords if wp[0] >= mid_lat]
        south_points = [wp for wp in waypoints_with_coords if wp[0] < mid_lat]

        # Sort each group by longitude to get west/east
        north_points.sort(key=lambda x: x[1])  # NW, NE
        south_points.sort(key=lambda x: x[1])  # SW, SE

        # Create rectangle: NW -> NE -> SE -> SW -> NW
        if len(north_points) >= 1 and len(south_points) >= 1:
            corners = []
            if len(north_points) == 2:
                corners.extend([north_points[0][2], north_points[1][2]])  # NW, NE
            else:
                corners.append(north_points[0][2])

            if len(south_points) == 2:
                corners.extend([south_points[1][2], south_points[0][2]])  # SE, SW
            else:
                corners.append(south_points[0][2])

            # Add coordinates for the polygon
            for corner in corners:
                kml_content += (
                    f'              {corner["longitude"]},{corner["latitude"]},0\n'
                )

            # Close the polygon by returning to first point
            if corners:
                first_corner = corners[0]
                kml_content += f'              {first_corner["longitude"]},{first_corner["latitude"]},0\n'

        kml_content += """            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
"""

    kml_content += """  </Document>
</kml>"""

    # Write KML file
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(kml_content)

    print(f"KML file created successfully: {output_file}")
    print(f"Mission: {mission_data['mission_id']}")
    print(f"Waypoints: {len(mission_data['waypoints'])}")

    # Print conditional task information
    if mission_data.get("conditional_tasks"):
        print("\nConditional Tasks Found:")
        for group_id, task_list in mission_data["conditional_tasks"].items():
            condition = mission_data.get("conditional_conditions", {}).get(
                group_id, "Unknown condition"
            )
            print(f"  {group_id}: {condition}")
            for task_id in task_list:
                print(f"    - {task_id}")
    else:
        print("No conditional tasks found.")


def main():
    """Main function to handle command line arguments and execute conversion."""
    parser = argparse.ArgumentParser(
        description="Convert XML mission files to KML for Google Earth visualization"
    )
    parser.add_argument("xml_file", help="Path to the XML mission file")
    parser.add_argument(
        "-o",
        "--output",
        help="Output KML file path (default: same name as input with .kml extension)",
        default=None,
    )

    args = parser.parse_args()

    # Validate input file
    if not os.path.exists(args.xml_file):
        print(f"Error: Input file '{args.xml_file}' does not exist.")
        return 1

    # Determine output file name
    if args.output is None:
        base_name = os.path.splitext(os.path.basename(args.xml_file))[0]
        output_dir = os.path.dirname(args.xml_file)
        args.output = os.path.join(output_dir, f"{base_name}.kml")

    try:
        # Parse XML and create KML
        print(f"Parsing XML file: {args.xml_file}")
        mission_data = parse_xml_mission(args.xml_file)

        if not mission_data["waypoints"]:
            print("Warning: No GPS waypoints found in the XML file.")
            return 1

        print(f"Found {len(mission_data['waypoints'])} waypoints")
        for waypoint in mission_data["waypoints"]:
            print(
                f"  - {waypoint['task_id']}: ({waypoint['latitude']}, {waypoint['longitude']})"
            )

        create_kml(mission_data, args.output)
        print(
            f"\nYou can now open '{args.output}' in Google Earth to visualize the mission."
        )

    except ET.ParseError as e:
        print(f"Error parsing XML file: {e}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
