from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


MAIN_MENU_BUTTONS = [
    ["⏰ Напоминания", "✅ Задачи"],
    ["🔁 Ритуалы", "🛒 Список покупок"],
    ["ℹ️ Помощь"],
]
REMINDERS_MENU_BUTTONS = [
    ["➕ Создать"],
    ["📅 На сегодня", "📆 На завтра"],
    ["📋 Все", "📦 Архив"],
]
TASKS_MENU_BUTTONS = [
    ["➕ Создать задачу"],
    ["📋 Все задачи"],
]
SHOPPING_MENU_BUTTONS = [
    ["➕ Добавить позицию"],
    ["📋 Посмотреть список"],
    ["📦 Архив"],
]
RITUALS_MENU_BUTTONS = [
    ["➕ Добавить ритуал"],
    ["📚 Мои ритуалы"],
]
BACK_HOME_ROW = ["⬅️ Назад", "🏠 На главную"]


def _with_back_home(builder: ReplyKeyboardBuilder, *, resize: bool = True) -> ReplyKeyboardMarkup:
    builder.row(*(KeyboardButton(text=text) for text in BACK_HOME_ROW))
    return builder.as_markup(resize_keyboard=resize)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for row in MAIN_MENU_BUTTONS:
        builder.row(*(KeyboardButton(text=text) for text in row))
    return _with_back_home(builder)


def reminders_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for row in REMINDERS_MENU_BUTTONS:
        builder.row(*(KeyboardButton(text=text) for text in row))
    return _with_back_home(builder)


def tasks_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for row in TASKS_MENU_BUTTONS:
        builder.row(*(KeyboardButton(text=text) for text in row))
    return _with_back_home(builder)


def shopping_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for row in SHOPPING_MENU_BUTTONS:
        builder.row(*(KeyboardButton(text=text) for text in row))
    return _with_back_home(builder)


def rituals_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    for row in RITUALS_MENU_BUTTONS:
        builder.row(*(KeyboardButton(text=text) for text in row))
    return _with_back_home(builder)


def simple_back_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(*(KeyboardButton(text=text) for text in BACK_HOME_ROW))
    return builder.as_markup(resize_keyboard=True)


def reminder_date_choice_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Сегодня", callback_data="date:today"),
        InlineKeyboardButton(text="Завтра", callback_data="date:tomorrow"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 На дату…", callback_data="date:calendar"),
    )
    return builder.as_markup()


def hours_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for hour in range(24):
        builder.button(text=f"{hour:02d}", callback_data=f"hour:{hour}")
    builder.adjust(6)
    return builder.as_markup()


def minutes_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for minute in (0, 5, 10, 15, 20, 30, 40, 45, 50):
        builder.button(text=f"{minute:02d}", callback_data=f"minute:{minute}")
    builder.adjust(4)
    return builder.as_markup()


ALERT_CALLBACK_PREFIX = "alert"
ALERT_DEFAULT_SELECTION = {"15", "0"}
ALERT_OPTIONS = [
    ("за 24 ч", "1440"),
    ("за 3 ч", "180"),
    ("за 1 ч", "60"),
    ("за 30 мин", "30"),
    ("за 15 мин", "15"),
    ("в момент", "0"),
]


def alerts_keyboard(selected: Iterable[str]) -> InlineKeyboardMarkup:
    selected_set = set(selected)
    builder = InlineKeyboardBuilder()
    for label, value in ALERT_OPTIONS:
        prefix = "✅" if value in selected_set else "☑️"
        builder.button(text=f"{prefix} {label}", callback_data=f"alert:{value}")
    builder.button(text="Готово", callback_data="alert:done")
    builder.adjust(1)
    return builder.as_markup()


@dataclass(slots=True)
class CalendarMonth:
    year: int
    month: int


def calendar_keyboard(month: CalendarMonth) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="◀️",
            callback_data=f"cal:prev:{month.year}:{month.month}",
        ),
        InlineKeyboardButton(
            text=f"{month.month:02d}.{month.year}",
            callback_data="cal:ignore:0:0",
        ),
        InlineKeyboardButton(
            text="▶️",
            callback_data=f"cal:next:{month.year}:{month.month}",
        ),
    )
    first_weekday, days_in_month = monthrange(month.year, month.month)
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    builder.row(*[InlineKeyboardButton(text=day, callback_data="cal:ignore:0:0") for day in weekdays])
    day_buttons: List[InlineKeyboardButton] = []
    # monthrange uses Monday=0
    for _ in range(first_weekday):
        day_buttons.append(InlineKeyboardButton(text=" ", callback_data="cal:ignore:0:0"))
    for day in range(1, days_in_month + 1):
        day_buttons.append(
            InlineKeyboardButton(
                text=f"{day:02d}",
                callback_data=f"cal:select:{month.year}:{month.month}:{day}",
            )
        )
    # Fill remaining slots to complete weeks
    while len(day_buttons) % 7 != 0:
        day_buttons.append(InlineKeyboardButton(text=" ", callback_data="cal:ignore:0:0"))
    for i in range(0, len(day_buttons), 7):
        builder.row(*day_buttons[i : i + 7])
    return builder.as_markup()


def task_item_keyboard(task_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"task:delete:{task_id}")
    return builder.as_markup()


def shopping_item_keyboard(item_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Куплено", callback_data=f"shop:done:{item_id}"),
        InlineKeyboardButton(text="❌ Удалить", callback_data=f"shop:delete:{item_id}"),
    )
    return builder.as_markup()


def reminder_actions_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Удалить", callback_data=f"rem:delete:{reminder_id}")
    return builder.as_markup()


def ritual_preset_keyboard(slug: str, pinned: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if pinned:
        builder.button(text="🔓 Открепить", callback_data=f"ritual:unpin:{slug}")
    else:
        builder.button(text="📌 Добавить в Напоминания", callback_data=f"ritual:add:{slug}")
    return builder.as_markup()


__all__ = [
    "ALERT_DEFAULT_SELECTION",
    "ALERT_OPTIONS",
    "CalendarMonth",
    "alerts_keyboard",
    "calendar_keyboard",
    "hours_keyboard",
    "main_menu_keyboard",
    "minutes_keyboard",
    "reminder_actions_keyboard",
    "reminder_date_choice_keyboard",
    "reminders_menu_keyboard",
    "rituals_menu_keyboard",
    "simple_back_keyboard",
    "shopping_menu_keyboard",
    "tasks_menu_keyboard",
    "task_item_keyboard",
    "shopping_item_keyboard",
    "ritual_preset_keyboard",
]
