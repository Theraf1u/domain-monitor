"""FSM states for every text-input flow across the bot - one shared
StatesGroup so a handler module never needs to guess which other module
"owns" a given waiting_for_* state; they're all listed here regardless of
which handlers/*.py file actually sets/reads them.
"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Inputs(StatesGroup):
    waiting_for_node_rename = State()
    waiting_for_node_search = State()
    waiting_for_domain_search = State()
    waiting_for_stats_custom_range = State()
    waiting_for_filter_pattern = State()
    waiting_for_filter_comment = State()
    waiting_for_filter_import = State()
    waiting_for_filter_check = State()
    waiting_for_export_range = State()
    waiting_for_custom_batch_seconds = State()
    waiting_for_quiet_hours_range = State()
    waiting_for_retention_days = State()
    waiting_for_offline_seconds = State()
    waiting_for_timezone_offset = State()
    waiting_for_buffer_thresholds = State()
    waiting_for_backup_interval = State()
    waiting_for_backup_keep = State()
    waiting_for_backup_group_chat_id = State()
    waiting_for_backup_group_topic_id = State()
