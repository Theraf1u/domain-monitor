"""Single shared Jinja2Templates instance, so every route module renders
against the same template directory and global functions/filters."""
from __future__ import annotations

import os

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)
