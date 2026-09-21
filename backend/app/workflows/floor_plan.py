from __future__ import annotations

from dataclasses import dataclass
from html import escape
from math import hypot

from backend.app.workflows.geometry import PortableRoom, PortableStructure, PortableSurface

WALL_PALETTE = (
    "#3B7BFF",
    "#FF8C33",
    "#33C766",
    "#E040A8",
    "#8C59F2",
    "#26BFBF",
    "#F2C14E",
    "#FF5C5C",
)
OPENING_COLORS = {
    "door": "#F4F4F5",
    "window": "#A8D8FF",
    "opening": "#D0D0D4",
}


@dataclass(frozen=True, slots=True)
class PlanPoint:
    x: float
    z: float


@dataclass(frozen=True, slots=True)
class PlanWall:
    index: int
    identifier: str
    start: PlanPoint
    end: PlanPoint
    length_m: float
    height_m: float
    compass: str
    color: str

    @property
    def length_cm(self) -> int:
        return round(self.length_m * 100)

    @property
    def midpoint(self) -> PlanPoint:
        return PlanPoint((self.start.x + self.end.x) / 2, (self.start.z + self.end.z) / 2)


@dataclass(frozen=True, slots=True)
class PlanOpening:
    index: int
    kind: str
    start: PlanPoint
    end: PlanPoint
    length_m: float
    wall_index: int | None
    color: str

    @property
    def length_cm(self) -> int:
        return round(self.length_m * 100)


@dataclass(frozen=True, slots=True)
class TaggedFloorPlan:
    room_labels: tuple[str, ...]
    walls: tuple[PlanWall, ...]
    openings: tuple[PlanOpening, ...]
    min_x: float
    max_x: float
    min_z: float
    max_z: float
    floor_area_m2: float
    floor_area_method: str
    ceiling_height_m: float | None

    @classmethod
    def from_structure(cls, structure: PortableStructure) -> TaggedFloorPlan:
        walls: list[PlanWall] = []
        openings: list[PlanOpening] = []
        heights: list[float] = []
        pending: list[tuple[PortableRoom, list[PlanWall]]] = []
        for room in structure.rooms:
            room_walls: list[PlanWall] = []
            for surface in room.walls:
                segment = _segment(surface)
                index = len(walls) + 1
                wall = PlanWall(
                    index=index,
                    identifier=surface.identifier,
                    start=segment[0],
                    end=segment[1],
                    length_m=float(surface.dimensions[0]),
                    height_m=float(surface.dimensions[1]),
                    compass="N",
                    color=WALL_PALETTE[(index - 1) % len(WALL_PALETTE)],
                )
                walls.append(wall)
                room_walls.append(wall)
                heights.append(wall.height_m)
            pending.append((room, room_walls))
        if walls:
            centroid = PlanPoint(
                sum(wall.midpoint.x for wall in walls) / len(walls),
                sum(wall.midpoint.z for wall in walls) / len(walls),
            )
            walls = [
                PlanWall(
                    index=wall.index,
                    identifier=wall.identifier,
                    start=wall.start,
                    end=wall.end,
                    length_m=wall.length_m,
                    height_m=wall.height_m,
                    compass=_outward_compass(wall, centroid),
                    color=wall.color,
                )
                for wall in walls
            ]
            pending = [
                (
                    room,
                    [
                        next(item for item in walls if item.index == wall.index)
                        for wall in room_walls
                    ],
                )
                for room, room_walls in pending
            ]
        for room, room_walls in pending:
            for kind in ("door", "window", "opening"):
                surfaces = {
                    "door": room.doors,
                    "window": room.windows,
                    "opening": room.openings,
                }[kind]
                for surface in surfaces:
                    try:
                        start, end = _segment(surface)
                    except ValueError:
                        continue
                    openings.append(
                        PlanOpening(
                            index=sum(1 for item in openings if item.kind == kind) + 1,
                            kind=kind,
                            start=start,
                            end=end,
                            length_m=float(surface.dimensions[0]),
                            wall_index=_parent_wall_index(surface, start, end, room_walls),
                            color=OPENING_COLORS[kind],
                        )
                    )
        if not walls:
            raise ValueError("processed RoomPlan structure contains no walls")
        xs = [point.x for wall in walls for point in (wall.start, wall.end)]
        zs = [point.z for wall in walls for point in (wall.start, wall.end)]
        area, method = _floor_area(walls, min(xs), max(xs), min(zs), max(zs))
        return cls(
            room_labels=tuple(room.label for room in structure.rooms),
            walls=tuple(walls),
            openings=tuple(openings),
            min_x=min(xs),
            max_x=max(xs),
            min_z=min(zs),
            max_z=max(zs),
            floor_area_m2=area,
            floor_area_method=method,
            ceiling_height_m=max(heights) if heights else None,
        )

    def summary_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "source": "roomplan/processed/structure.json",
            "room_count": len(self.room_labels),
            "wall_count": len(self.walls),
            "bounds_metres": {
                "width": self.max_x - self.min_x,
                "depth": self.max_z - self.min_z,
            },
            "floor_area_estimate_square_metres": self.floor_area_m2,
            "floor_area_method": self.floor_area_method,
            "ceiling_height_metres": self.ceiling_height_m,
            "compass": "scan_+Z",
            "compass_note": "N is scan +Z, not magnetic north.",
            "status": "estimated",
            "walls": [
                {
                    "index": wall.index,
                    "length_cm": wall.length_cm,
                    "compass": wall.compass,
                    "color": wall.color,
                }
                for wall in self.walls
            ],
            "openings": [
                {
                    "kind": opening.kind,
                    "index": opening.index,
                    "length_cm": opening.length_cm,
                    "wall_index": opening.wall_index,
                }
                for opening in self.openings
            ],
        }


def render_svg(plan: TaggedFloorPlan, overlays: list | None = None) -> str:
    scale = 100.0
    padding = 48.0
    drawing_width = max((plan.max_x - plan.min_x) * scale + padding * 2, 220)
    drawing_height = max((plan.max_z - plan.min_z) * scale + padding * 2, 220)
    legend_lines = _legend_lines(plan, overlays or [])
    legend_height = 28 + 18 * len(legend_lines)
    width = drawing_width
    height = drawing_height + legend_height

    def screen(point: PlanPoint) -> tuple[float, float]:
        return (
            (point.x - plan.min_x) * scale + padding,
            (plan.max_z - point.z) * scale + padding,
        )

    centroid = PlanPoint(
        sum(wall.midpoint.x for wall in plan.walls) / len(plan.walls),
        sum(wall.midpoint.z for wall in plan.walls) / len(plan.walls),
    )
    wall_markup = []
    labels = []
    for wall in plan.walls:
        x1, y1 = screen(wall.start)
        x2, y2 = screen(wall.end)
        wall_markup.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{wall.color}" data-wall="{wall.index}" />'
        )
        mx, my = screen(wall.midpoint)
        ox, oy = _label_offset(wall, centroid, scale)
        labels.append(
            f'<text x="{mx + ox:.2f}" y="{my + oy:.2f}" fill="{wall.color}" '
            f'text-anchor="middle" dominant-baseline="middle">{wall.index}</text>'
        )
    overlay_markup = []
    for overlay in overlays or []:
        x1, y1 = screen(PlanPoint(overlay.min_x, overlay.max_z))
        x2, y2 = screen(PlanPoint(overlay.max_x, overlay.min_z))
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        fill = "#3B7BFF33" if overlay.status == "ok" else "#F2C14E55"
        overlay_markup.append(
            f'<rect x="{left:.2f}" y="{top:.2f}" width="{right - left:.2f}" '
            f'height="{bottom - top:.2f}" fill="{fill}" stroke="#E8E8ED" '
            f'stroke-width="1.5" data-shelf="{escape(overlay.face_id)}" />'
        )
        overlay_markup.append(
            f'<text x="{(left + right) / 2:.2f}" y="{(top + bottom) / 2:.2f}" '
            f'fill="#F4F4F5" font-size="11" text-anchor="middle" '
            f'dominant-baseline="middle">{escape(overlay.copy_count_label)}</text>'
        )
    opening_markup = []
    for opening in plan.openings:
        x1, y1 = screen(opening.start)
        x2, y2 = screen(opening.end)
        opening_markup.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{opening.color}" data-{opening.kind}="{opening.index}" />'
        )
    compass_x = drawing_width - 28
    compass_y = 32
    legend = []
    cursor = drawing_height + 18
    for line in legend_lines:
        legend.append(
            f'<text x="{padding:.0f}" y="{cursor:.0f}" fill="{line[0]}">{escape(line[1])}</text>'
        )
        cursor += 18
    room_names = ", ".join(escape(label) for label in plan.room_labels)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.2f} {height:.2f}" '
        'role="img" aria-labelledby="title desc">\n'
        '  <title id="title">Tagged RoomPlan floor plan</title>\n'
        f'  <desc id="desc">Numbered walls and openings for {room_names}. '
        "N is scan +Z, not magnetic north.</desc>\n"
        '  <rect width="100%" height="100%" fill="#1C1C1E" />\n'
        '  <g stroke-width="8" stroke-linecap="square">\n    '
        + "\n    ".join(wall_markup)
        + "\n  </g>\n"
        "  <g>\n    "
        + "\n    ".join(overlay_markup)
        + "\n  </g>\n"
        '  <g stroke-width="10" stroke-linecap="butt">\n    '
        + "\n    ".join(opening_markup)
        + "\n  </g>\n"
        '  <g font-family="-apple-system, Helvetica, sans-serif" '
        'font-size="14" font-weight="700">\n    '
        + "\n    ".join(labels)
        + "\n  </g>\n"
        f'  <g transform="translate({compass_x:.1f},{compass_y:.1f})" fill="#E8E8ED" '
        'font-family="-apple-system, Helvetica, sans-serif" font-size="13" font-weight="700" '
        'text-anchor="middle">\n'
        '    <line x1="0" y1="10" x2="0" y2="-12" stroke="#E8E8ED" stroke-width="2" />\n'
        '    <polygon points="0,-16 -4,-8 4,-8" fill="#E8E8ED" />\n'
        '    <text x="0" y="-20">N</text>\n'
        "  </g>\n"
        '  <g font-family="-apple-system, Helvetica, sans-serif" font-size="12">\n    '
        + "\n    ".join(legend)
        + "\n  </g>\n</svg>\n"
    )


def _legend_lines(
    plan: TaggedFloorPlan, overlays: list | None = None
) -> list[tuple[str, str]]:
    ceiling = (
        f"{round(plan.ceiling_height_m * 100)} cm"
        if plan.ceiling_height_m is not None
        else "unknown"
    )
    lines = [
        (
            "#9A9AA2",
            (
                f"{len(plan.walls)} walls · {plan.floor_area_m2:.1f} m² · "
                f"ceiling {ceiling}. N is scan +Z, not magnetic north."
            ),
        ),
        ("#E8E8ED", "Walls"),
    ]
    for wall in plan.walls:
        lines.append(
            (wall.color, f"Wall {wall.index}  {wall.length_cm} cm · {wall.compass}")
        )
    grouped: dict[str, list[PlanOpening]] = {"door": [], "window": [], "opening": []}
    for opening in plan.openings:
        grouped[opening.kind].append(opening)
    titles = {"door": "Doors", "window": "Windows", "opening": "Openings"}
    for kind, items in grouped.items():
        if not items:
            continue
        lines.append(("#E8E8ED", titles[kind]))
        for opening in items:
            wall = f" · on wall {opening.wall_index}" if opening.wall_index else ""
            lines.append(
                (
                    opening.color,
                    f"{kind.title()} {opening.index}  {opening.length_cm} cm{wall}",
                )
            )
    if overlays:
        lines.append(("#E8E8ED", "Shelves"))
        for overlay in overlays:
            lines.append(
                (
                    "#F2C14E" if overlay.status != "ok" else "#3B7BFF",
                    f"{overlay.label}  {overlay.copy_count_label}  {overlay.fill_label}",
                )
            )
    return lines


def _segment(surface: PortableSurface) -> tuple[PlanPoint, PlanPoint]:
    width = surface.dimensions[0]
    transform = surface.transform
    center_x, center_z = transform[12], transform[14]
    axis_x, axis_z = transform[0], transform[2]
    length = hypot(axis_x, axis_z)
    if length < 1e-8:
        raise ValueError(f"surface {surface.identifier} has a degenerate transform")
    axis_x, axis_z = axis_x / length, axis_z / length
    half = width / 2
    return (
        PlanPoint(center_x - axis_x * half, center_z - axis_z * half),
        PlanPoint(center_x + axis_x * half, center_z + axis_z * half),
    )


def _outward_compass(wall: PlanWall, centroid: PlanPoint) -> str:
    tx = wall.end.x - wall.start.x
    tz = wall.end.z - wall.start.z
    length = hypot(tx, tz) or 1.0
    px, pz = -tz / length, tx / length
    mid = wall.midpoint
    if (mid.x - centroid.x) * px + (mid.z - centroid.z) * pz < 0:
        px, pz = -px, -pz
    return _compass(px, pz)


def _compass(nx: float, nz: float) -> str:
    if abs(nz) >= abs(nx):
        return "N" if nz > 0 else "S"
    return "E" if nx > 0 else "W"


def _parent_wall_index(
    surface: PortableSurface,
    start: PlanPoint,
    end: PlanPoint,
    walls: list[PlanWall],
) -> int | None:
    parent = surface.parent_identifier
    if parent:
        for wall in walls:
            if wall.identifier == parent:
                return wall.index
    mid = PlanPoint((start.x + end.x) / 2, (start.z + end.z) / 2)
    best_index = None
    best_distance = 0.35
    for wall in walls:
        distance = _point_segment_distance(mid, wall.start, wall.end)
        if distance < best_distance:
            best_distance = distance
            best_index = wall.index
    return best_index


def _point_segment_distance(point: PlanPoint, start: PlanPoint, end: PlanPoint) -> float:
    dx, dz = end.x - start.x, end.z - start.z
    length_sq = dx * dx + dz * dz
    if length_sq < 1e-12:
        return hypot(point.x - start.x, point.z - start.z)
    t = ((point.x - start.x) * dx + (point.z - start.z) * dz) / length_sq
    t = min(1.0, max(0.0, t))
    return hypot(point.x - (start.x + t * dx), point.z - (start.z + t * dz))


def _label_offset(wall: PlanWall, centroid: PlanPoint, scale: float) -> tuple[float, float]:
    tx, tz = wall.end.x - wall.start.x, wall.end.z - wall.start.z
    length = hypot(tx, tz) or 1.0
    px, pz = -tz / length, tx / length
    mid = wall.midpoint
    if (mid.x - centroid.x) * px + (mid.z - centroid.z) * pz < 0:
        px, pz = -px, -pz
    return px * 16, -pz * 16


def _floor_area(
    walls: list[PlanWall],
    min_x: float,
    max_x: float,
    min_z: float,
    max_z: float,
) -> tuple[float, str]:
    polygon = _ordered_polygon(walls)
    if polygon and len(polygon) >= 3:
        area = abs(
            sum(
                polygon[index].x * polygon[(index + 1) % len(polygon)].z
                - polygon[(index + 1) % len(polygon)].x * polygon[index].z
                for index in range(len(polygon))
            )
            / 2
        )
        if area > 0.05:
            return area, "shoelace_wall_polygon"
    return (max_x - min_x) * (max_z - min_z), "axis_aligned_roomplan_bounds"


def _ordered_polygon(walls: list[PlanWall]) -> list[PlanPoint] | None:
    if not walls:
        return None
    snap = 0.08
    unused = list(walls)
    points = [unused[0].start, unused[0].end]
    unused.pop(0)
    while unused:
        current = points[-1]
        match_index = None
        match_point: PlanPoint | None = None
        for index, wall in enumerate(unused):
            if hypot(wall.start.x - current.x, wall.start.z - current.z) <= snap:
                match_index = index
                match_point = wall.end
                break
            if hypot(wall.end.x - current.x, wall.end.z - current.z) <= snap:
                match_index = index
                match_point = wall.start
                break
        if match_index is None or match_point is None:
            return None
        points.append(match_point)
        unused.pop(match_index)
    if hypot(points[0].x - points[-1].x, points[0].z - points[-1].z) > snap:
        return None
    return points[:-1]
