"""Session manager for manual start/stop monitoring workflow."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import config


@dataclass
class SessionState:
    session_id: Optional[str] = None
    food_name: Optional[str] = None
    selected_sensors: List[str] = field(default_factory=list)
    status: str = "idle"  # idle | running | completed
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    device_id: str = config.DEVICE_ID


class SessionManager:
    def __init__(self, state_file: Path | None = None):
        self.state_file = state_file or config.SESSION_FILE
        self.state = SessionState()
        self.load_state()

    def load_state(self) -> SessionState:
        if self.state_file.exists():
            try:
                raw = json.loads(self.state_file.read_text())
                self.state = SessionState(**raw)
            except Exception:
                self.state = SessionState()
        return self.state

    def save_state(self) -> None:
        self.state_file.write_text(json.dumps(asdict(self.state), indent=2))

    def start(self, food_name: str, selected_sensors: List[str]) -> Dict[str, Any]:
        session_id = str(int(time.time()))
        ordered_sensors = [s for s in config.SELECTABLE_GAS_SENSORS if s in selected_sensors]
        self.state = SessionState(
            session_id=session_id,
            food_name=food_name,
            selected_sensors=ordered_sensors,
            status="running",
            start_time=int(time.time()),
            end_time=None,
        )
        self.save_state()
        return asdict(self.state)

    def stop(self) -> Dict[str, Any]:
        self.state.status = "completed"
        self.state.end_time = int(time.time())
        self.save_state()
        return asdict(self.state)

    def clear(self) -> Dict[str, Any]:
        self.state = SessionState()
        self.save_state()
        return asdict(self.state)

    def get(self) -> Dict[str, Any]:
        return asdict(self.state)

    def is_running(self) -> bool:
        return self.state.status == "running"
