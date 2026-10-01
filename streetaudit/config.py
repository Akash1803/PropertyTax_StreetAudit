"""Settings for one audit run. Paths and thresholds come from a TOML file, API keys from the environment."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # --- inputs
    buildings: Path
    geocodes: Path
    run_dir: Path
    other_footprints: tuple[Path, ...] = ()   # footprints of neighbouring wards, used only to block sight lines
    id_field: str = "GIS_ID"
    usage_field: str = "Building_U"
    storeys_field: str = "Total_Floo"          # counts storeys: 0 = vacant land, 1 = ground only, 2 = G+1
    road_field: str = "RoadName"
    door_fields: tuple[str, ...] = ("NewDoorNo", "OldDoorNo")
    assess_usage_field: str = "Assessment"
    metric_epsg: int = 32643

    # --- panoramas
    search_m: float = 40.0        # panoramas are looked for within this distance of a building
    grid_m: float = 5.0           # spacing of the points where the image source is asked for its nearest panorama
    grid_radius_m: float = 10.0

    # --- views
    max_view_dist: float = 30.0   # a panorama farther than this is never used
    near_view_dist: float = 15.0  # a building needs one view at least this close to count as street-facing
    min_view_dist: float = 1.5    # in a narrow lane the panorama in front of a house is 2 m from its wall
    min_visible_m: float = 3.0
    min_span_deg: float = 12.0    # below this the wall is seen end-on
    max_span_deg: float = 120.0   # the widest image the source gives
    max_snap_m: float = 2.5       # a panorama plotting this deep inside a footprint is moved out; deeper ones are dropped
    max_views: int = 4
    min_new_wall_m: float = 2.0   # another view is added only if it shows this much wall not yet seen square-on
    outline_step_m: float = 0.5
    min_part_m2: float = 15.0     # smaller parts of a multi-part footprint are not audited on their own

    # --- imaging
    image_px: int = 640
    camera_height_m: float = 2.5
    position_error_m: float = 2.0  # how far the panorama position may be off along the street
    chip_half_m: float = 45.0
    chip_px: int = 900

    # --- LLM
    llm_models: tuple[str, ...] = ("gemini-pro-latest",)
    llm_workers: int = 6
    second_opinion: str = "verify"  # "verify": ask again before a building is Verified; "all"; "none"
    door_unique_m: float = 50.0
    old_imagery_before: str = "2025-01"  # views captured before this month are "old imagery" in a mismatch

    # --- street level
    roads: Path | None = None      # the analyst's road line layer; only read
    road_name_field: str = "Road_Name"
    zone_field: str = "Zone"       # tax zone of each assessment in the geocodes
    junction_tol_m: float = 1.0    # a line end this close to another line makes a junction
    min_stretch_m: float = 10.0    # shorter pieces between junctions are dropped
    street_cam_m: float = 15.0     # a building belongs to the stretch within this distance of its closest camera
    backlot_m: float = 30.0        # ... or, when no street view can check it, of its footprint
    street_step_m: float = 50.0    # spacing of the along-road pictures
    street_pano_m: float = 10.0    # a picture point uses a panorama within this distance
    street_fov: float = 90.0
    gap_step_m: float = 10.0       # sampling of the gap between building fronts
    gap_reach_m: float = 25.0

    @classmethod
    def load(cls, path: str | Path) -> "Settings":
        # utf-8-sig: Windows editors and PowerShell often put a byte-order mark at the start of the file
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
        known = {f.name: f for f in fields(cls)}
        unknown = set(raw) - set(known)
        if unknown:
            raise ValueError(f"unknown settings in {path}: {sorted(unknown)}")
        values = {}
        for name, value in raw.items():
            if name in ("buildings", "geocodes", "run_dir", "roads"):
                value = Path(value)
            elif name == "other_footprints":
                value = tuple(Path(v) for v in value)
            elif isinstance(value, list):
                value = tuple(value)
            values[name] = value
        return cls(**values)

    @property
    def work_dir(self) -> Path:
        return self.run_dir / "work"

    @property
    def evidence_dir(self) -> Path:
        return self.run_dir / "evidence"


def load_env(path: Path | None = None) -> None:
    """Read KEY=VALUE lines from the project's .env file into the environment.

    The .env file wins over variables already set on the machine: a stale machine-wide key would
    otherwise silently replace the key chosen for this project.
    """
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ[key.strip()] = value


def require_key(name: str) -> str:
    load_env()
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is not set. Put it in the environment or in {REPO_ROOT / '.env'} (see .env.example).")
    return value
