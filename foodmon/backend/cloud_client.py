"""Cloud database client.

Default path supports Firebase Realtime Database REST API because it is simple for
ESP and Raspberry Pi. A generic REST placeholder is also included.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

import config


class CloudClient:
    def __init__(self) -> None:
        self.enabled = config.ENABLE_CLOUD_SYNC
        self.provider = config.CLOUD_PROVIDER.lower()
        self.timeout = config.REQUEST_TIMEOUT

    def _firebase_url(self, path: str) -> str:
        base = config.FIREBASE_DB_URL.rstrip("/")
        auth = f"?auth={config.FIREBASE_AUTH}" if config.FIREBASE_AUTH else ""
        return f"{base}/{path.lstrip('/')}.json{auth}"

    def _generic_url(self, path: str) -> str:
        return f"{config.GENERIC_CLOUD_BASE_URL.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if config.GENERIC_CLOUD_API_KEY:
            headers["Authorization"] = f"Bearer {config.GENERIC_CLOUD_API_KEY}"
        return headers

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        if not self.enabled:
            return None

        if self.provider == "firebase":
            url = self._firebase_url(path)
        else:
            url = self._generic_url(path)

        response = requests.request(
            method=method,
            url=url,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        if response.text:
            try:
                return response.json()
            except json.JSONDecodeError:
                return response.text
        return None

    def upsert_session(self, session: Dict[str, Any]) -> None:
        self._request("PUT", f"sessions/{session['session_id']}", session)

    def append_reading(self, session_id: str, reading: Dict[str, Any]) -> Any:
        return self._request("POST", f"readings/{session_id}", reading)

    def append_event(self, session_id: str, event: Dict[str, Any]) -> Any:
        return self._request("POST", f"events/{session_id}", event)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        result = self._request("GET", f"sessions/{session_id}")
        return result if isinstance(result, dict) else None

    def get_latest_session(self) -> Optional[Dict[str, Any]]:
        if self.provider == "firebase":
            result = self._request("GET", "sessions")
            if not isinstance(result, dict) or not result:
                return None
            # choose latest by start_time/session_id
            latest_key = max(result, key=lambda k: result[k].get("start_time", 0))
            return result[latest_key]
        result = self._request("GET", "sessions/latest")
        return result if isinstance(result, dict) else None
