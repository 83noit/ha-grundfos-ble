"""Constants for the Grundfos Bluetooth integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "grundfos_ble"

CONF_MODEL: Final = "model"
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_SOFTWARE_VERSION: Final = "software_version"
CONF_BLE_SOFTWARE_VERSION: Final = "ble_software_version"
CONF_UNIT_NAME: Final = "unit_name"
CONF_CAPABILITIES: Final = "capabilities"
CONF_PAIRED_SOURCE: Final = "paired_source"
CONF_PAIRED_AT: Final = "paired_at"

OPT_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 600

BUTTON_WAIT_TIMEOUT: Final = 60.0
"""Seconds the pairing step waits for the pump's Bluetooth button."""
BUTTON_FLAG_MAX_AGE: Final = 1.0
"""A button-flag advertisement older than this has used up too much of the 3 s window."""
BUTTON_WINDOW: Final = 4.0
"""Seconds the pump shows the button flag after a press; it accepts pairing inside that."""
PAIR_CONNECT_TIMEOUT: Final = 5.0

MAX_PAIRING_FAILURES: Final = 3
"""Consecutive polls refused at the encryption step before a re-pairing flow is raised."""
REAUTH_BACKOFF: Final = 600
"""Seconds between polls while re-pairing is outstanding, so a recovery is still noticed."""
ADVERT_STALE_SECONDS: Final = 40.0
"""With no advertisement for this long the pump is treated as gone (it advertises every ~8 s)."""
RETRY_PAUSE: Final = 0.25
"""Seconds to let the Bluetooth stack settle before a poll retries on a fresh link."""
POLL_TIMEOUT: Final = 75.0
"""Upper bound on one poll, connection attempts included.

Above the worst case of two links, each with a retried connect (2 x 10 s), the settle pause and
one 10 s request timeout (~62 s), so the retry on a fresh link is not cut short.
"""
FAILURES_BEFORE_UNAVAILABLE: Final = 3
"""Consecutive failed polls before entities report unavailable (one or two misses are noise)."""
FORGETFUL_PROXY_WINDOW: Final = 1800.0
"""Seconds after pairing in which a refusal on the bonded path, after a good poll, means the
proxy forgot the bond (ESP-IDF 5.5.6 does so after one session)."""
OUTAGE_ISSUE_AFTER: Final = 3600.0
"""Seconds of failed polls, while the pump is heard and connectable, before a repair issue."""
MAX_SILENT_BACKOFF: Final = 32
"""Most polls an optional object that went silent is skipped for (1, 2, 4 ... 32)."""

ISSUE_PROXY_FORGETS_BOND: Final = "proxy_forgets_bond"
ISSUE_PROXY_CANNOT_PAIR: Final = "proxy_cannot_pair"
ISSUE_UNREACHABLE: Final = "unreachable"

CAP_OPERATION: Final = "operation"
CAP_ALARMS: Final = "alarms"
CAP_WARNINGS: Final = "warnings"
CAP_STATISTICS: Final = "statistics"
CAP_TEMPERATURES: Final = "temperatures"
CAP_ENERGY: Final = "energy"
CAP_AUTONIGHT: Final = "autonight"
CAP_MAX_FLOW_LIMITER: Final = "max_flow_limiter"
CAP_MIN_FLOW_LIMITER: Final = "min_flow_limiter"
