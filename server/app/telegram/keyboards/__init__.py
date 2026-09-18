"""Re-exports every keyboards/*.py module's public names at package level,
so external code keeps doing `from app.telegram import keyboards as kb` and
`kb.function_name(...)` exactly as when this was one file - only the
internal layout changed.
"""
from __future__ import annotations

from app.telegram.keyboards.backups import *  # noqa: F401,F403
from app.telegram.keyboards.common import *  # noqa: F401,F403
from app.telegram.keyboards.domains import *  # noqa: F401,F403
from app.telegram.keyboards.filters import *  # noqa: F401,F403
from app.telegram.keyboards.nodes import *  # noqa: F401,F403
from app.telegram.keyboards.notifications import *  # noqa: F401,F403
from app.telegram.keyboards.settings import *  # noqa: F401,F403
from app.telegram.keyboards.stats import *  # noqa: F401,F403
