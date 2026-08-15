"""Import the integration's protocol module without installing anything.

The tools deliberately share one implementation with the integration rather than
keeping a second copy in sync.
"""

import importlib.util
import pathlib
import sys

_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "lepro_ble"
    / "protocol.py"
)

_spec = importlib.util.spec_from_file_location("lepro_protocol", _PATH)
protocol = importlib.util.module_from_spec(_spec)
sys.modules["lepro_protocol"] = protocol
_spec.loader.exec_module(protocol)
