from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from zoneinfo import ZoneInfo

from keyboards import (
    ALERT_DEFAULT_SELECTION,
    ALERT_OPTIONS,
    CalendarMonth,
    alerts_keyboard,
    calendar_keyboard,
    hours_keyboard,
    main_menu_keyboard,
    minutes_keyboard,
    reminder_actions_keyboard,
    reminder_date_choice_keyboard,
    reminders_menu_keyboard,
    rituals_menu_keyboard,
    shopping_menu_keyboard,
    simple_back_keyboard,
    tasks_menu_keyboard,
    task_item_keyboard,
    shopping_item_keyboard,
    ritual_preset_keyboard,
)
from scheduler import SchedulerManager
from storage import DBManager, Reminder, Task, ShoppingItem

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "mentor.db"
KYIV_TZ = ZoneInfo("Europe/Kyiv")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True, slots=True)
class RitualPreset:
    slug: str
    title: str
    goal: str
    steps: str
    recommendation_time: time
    duration: str


RITUAL_PRESETS: List[RitualPreset] = [
    RitualPreset(
        slug="morning_focus",
        title="Утро 20–25 мин",
        goal="Фокус и энергия",
        steps="Дыхание 4×4×4 → Журнал изобилия → 1 шаг к деньгам",
        recommendation_time=time(8, 0),
        duration="20–25 мин",
    ),
    RitualPreset(
        slug="evening_reset",
        title="Вечер 5–10 мин",
        goal="Стабильность и завершение дня",
        steps="Музыка → Благодарность → Якорь состояния",
        recommendation_time=time(21, 30),
        duration="5–10 мин",
    ),
    RitualPreset(
        slug="single_focus",
        title="Одна мысль (5 мин)",
        goal="Концентрация",
        steps="Метод «Одна мысль» — таймер 5 минут",
        recommendation_time=time(12, 0),
        duration="5 мин",
    ),
    RitualPreset(
        slug="warm_money",
        title="Тёплый денежный дождь",
        goal="Финансовая смелость и видение",
        steps="Визуализация «тёплый денежный дождь»",
        recommendation_time=time(19, 0),
        duration="7–10 мин",
    ),
]

RITUAL_PRESET_MAP = {preset.slug: preset for preset in RITUAL_PRESETS}


@dataclass(slots=True)
class ReminderDraft:
    target_date: Optional[date] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    alerts: set[str] = None

    def __post_init__(self) -> None:
        if self.alerts is None:
            self.alerts = set(ALERT_DEFAULT_SELECTION)

    @property
    def is_complete(self) -> bool:
        return (
            self.target_date is not None
            and self.hour is not None
            and self.minute is not None
            and self.alerts
        )

    def build_event_datetime(self) -> datetime:
        if not self.is_complete:
            raise ValueError("Draft is not complete")
        local_dt = datetime.combine(
            self.target_date,
            time(self.hour, self.minute),
            tzinfo=KYIV_TZ,
        )
        return local_dt.astimezone(UTC)


class ReminderCreation(StatesGroup):
    choosing_date = State()
    choosing_custom_date = State()
    choosing_hour = State()
    choosing_minute = State()
    choosing_alerts = State()
    entering_text = State()


class SimpleTextState(StatesGroup):
    awaiting_task_text = State()
    awaiting_ritual_text = State()
    awaiting_shopping_text = State()


router = Router()
db_manager = DBManager(DB_PATH)
scheduler: SchedulerManager | None = None


async def show_main_menu(message: Message) -> None:
    await message.answer(
        "Привет! Я твой бот-наставник. Выбери раздел 👇",
        reply_markup=main_menu_keyboard(),
    )


async def show_reminders_menu(message: Message) -> None:
    await message.answer("Раздел «Напоминания». Что делаем?", reply_markup=reminders_menu_keyboard())


async def show_tasks_menu(message: Message) -> None:
    await message.answer("Раздел «Задачи». Что делаем?", reply_markup=tasks_menu_keyboard())


async def show_shopping_menu(message: Message) -> None:
    await message.answer("Раздел «Список покупок». Что делаем?", reply_markup=shopping_menu_keyboard())


async def show_rituals_menu(message: Message) -> None:
    await message.answer("Раздел «Ритуалы». Что делаем?", reply_markup=rituals_menu_keyboard())


async def reset_state(state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.clear()


def format_reminder_card(reminder: Reminder) -> str:
    local_dt = reminder.event_ts_utc.astimezone(KYIV_TZ)
    text = html.escape(reminder.text)
    return (
        f"<b>{local_dt.strftime('%d.%m.%Y')} · {local_dt.strftime('%H:%M')}</b>\n"
        f"{text}"
    )


def format_task_entry(task: Task, index: int) -> str:
    text = html.escape(task.text)
    return f"{index}. {text} (id={task.id})"


def format_shopping_entry(item: ShoppingItem, index: int) -> str:
    text = html.escape(item.text)
    return f"{index}. {text} (id={item.id})"


def compute_alert_datetimes(event_dt_utc: datetime, selected: Iterable[str]) -> List[datetime]:
    now_utc = datetime.now(tz=UTC)
    alerts: List[datetime] = []
    for _label, value in ALERT_OPTIONS:
        minutes = int(value)
        if value not in selected:
            continue
        alert_time = event_dt_utc - timedelta(minutes=minutes)
        if alert_time > now_utc:
            alerts.append(alert_time)
    return alerts


def get_next_occurrence(local_time: time) -> datetime:
    now_local = datetime.now(tz=KYIV_TZ)
    candidate = datetime.combine(now_local.date(), local_time, tzinfo=KYIV_TZ)
    if candidate <= now_local:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


def format_ritual_preset_card(preset: RitualPreset, pinned: bool) -> str:
    status = "📌 В напоминаниях" if pinned else "🔔 Можно добавить"
    return (
        f"<b>{preset.title}</b>\n"
        f"Цель: {preset.goal}\n"
        f"Шаги: {preset.steps}\n"
        f"Время: {preset.recommendation_time.strftime('%H:%M')} · длительность {preset.duration}\n"
        f"Статус: {status}"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await reset_state(state)
    await show_main_menu(message)


@router.message(F.text == "🏠 На главную")
async def go_home(message: Message, state: FSMContext) -> None:
    await reset_state(state)
    await show_main_menu(message)


@router.message(F.text == "⬅️ Назад")
async def go_back(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("Мы уже на главной.", reply_markup=main_menu_keyboard())
        return
    if current == ReminderCreation.entering_text:
        await state.set_state(ReminderCreation.choosing_alerts)
        data = await state.get_data()
        draft: ReminderDraft = data.get("draft")
        await message.answer("Выбери уведомления:", reply_markup=simple_back_keyboard())
        await message.answer(
            "Когда напомнить?", reply_markup=alerts_keyboard(draft.alerts)
        )
    elif current == ReminderCreation.choosing_alerts:
        await state.set_state(ReminderCreation.choosing_minute)
        await message.answer("Теперь выбери минуты:", reply_markup=simple_back_keyboard())
        await message.answer("Минуты:", reply_markup=minutes_keyboard())
    elif current == ReminderCreation.choosing_minute:
        await state.set_state(ReminderCreation.choosing_hour)
        await message.answer("Выбери час:", reply_markup=simple_back_keyboard())
        await message.answer("Часы:", reply_markup=hours_keyboard())
    elif current in (ReminderCreation.choosing_hour, ReminderCreation.choosing_custom_date):
        await state.set_state(ReminderCreation.choosing_date)
        await message.answer(
            "Когда напомнить?", reply_markup=reminder_date_choice_keyboard()
        )
    elif current == SimpleTextState.awaiting_task_text.state:
        await state.clear()
        await show_tasks_menu(message)
    elif current == SimpleTextState.awaiting_ritual_text.state:
        await state.clear()
        await show_rituals_menu(message)
    elif current == SimpleTextState.awaiting_shopping_text.state:
        await state.clear()
        await show_shopping_menu(message)
    else:
        await state.clear()
        await show_reminders_menu(message)


@router.message(F.text == "⏰ Напоминания")
async def reminders_entry(message: Message, state: FSMContext) -> None:
    await reset_state(state)
    await show_reminders_menu(message)


@router.message(F.text == "ℹ️ Помощь")
async def help_handler(message: Message) -> None:
    await message.answer(
        "Я помогу с напоминаниями, задачами и списками. Начни с выбора раздела."
    )


@router.message(F.text == "➕ Создать")
async def start_reminder_creation(message: Message, state: FSMContext) -> None:
    await state.set_state(ReminderCreation.choosing_date)
    draft = ReminderDraft()
    await state.update_data(draft=draft, calendar_month=None)
    await message.answer("Создаём новое напоминание.", reply_markup=simple_back_keyboard())
    await message.answer(
        "Выбери дату для напоминания:",
        reply_markup=reminder_date_choice_keyboard(),
    )


@router.callback_query(F.data.startswith("date:"))
async def handle_date_choice(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    draft: ReminderDraft = data.get("draft")
    if not draft:
        draft = ReminderDraft()
        await state.update_data(draft=draft)
    choice = callback.data.split(":", 1)[1]
    today = datetime.now(tz=KYIV_TZ).date()
    if choice == "today":
        draft.target_date = today
        await state.set_state(ReminderCreation.choosing_hour)
        await callback.message.edit_text("Выбран сегодня. Теперь час:")
        await callback.message.answer("Выбери час:", reply_markup=hours_keyboard())
    elif choice == "tomorrow":
        draft.target_date = today + timedelta(days=1)
        await state.set_state(ReminderCreation.choosing_hour)
        await callback.message.edit_text("Завтра так завтра! Час?")
        await callback.message.answer("Выбери час:", reply_markup=hours_keyboard())
    elif choice == "calendar":
        await state.set_state(ReminderCreation.choosing_custom_date)
        month = data.get("calendar_month")
        if not month:
            month = CalendarMonth(year=today.year, month=today.month)
            await state.update_data(calendar_month=month)
        await callback.message.edit_text(
            "Выбери дату на календаре:",
            reply_markup=calendar_keyboard(month),
        )


def shift_month(month: CalendarMonth, delta: int) -> CalendarMonth:
    new_month = month.month + delta
    year = month.year
    while new_month < 1:
        new_month += 12
        year -= 1
    while new_month > 12:
        new_month -= 12
        year += 1
    return CalendarMonth(year=year, month=new_month)


@router.callback_query(F.data.startswith("cal:"))
async def handle_calendar(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    action = parts[1]
    if action not in {"done", "delete"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    if action not in {"done", "delete"}:
        await callback.answer("Неизвестное действие", show_alert=True)
        return
    if action == "ignore":
        await callback.answer()
        return
    data = await state.get_data()
    month: CalendarMonth = data.get("calendar_month")
    if not month:
        today = datetime.now(tz=KYIV_TZ).date()
        month = CalendarMonth(year=today.year, month=today.month)
    if action == "prev":
        month = shift_month(month, -1)
        await state.update_data(calendar_month=month)
        await callback.message.edit_reply_markup(reply_markup=calendar_keyboard(month))
        await callback.answer()
    elif action == "next":
        month = shift_month(month, 1)
        await state.update_data(calendar_month=month)
        await callback.message.edit_reply_markup(reply_markup=calendar_keyboard(month))
        await callback.answer()
    elif action == "select":
        year = int(parts[2])
        month_num = int(parts[3])
        day = int(parts[4])
        draft: ReminderDraft = data.get("draft")
        if not draft:
            draft = ReminderDraft()
        draft.target_date = date(year, month_num, day)
        await state.update_data(draft=draft, calendar_month=CalendarMonth(year=year, month=month_num))
        await state.set_state(ReminderCreation.choosing_hour)
        await callback.message.edit_text(
            f"Дата выбрана: {draft.target_date.strftime('%d.%m.%Y')}. Теперь час:",
        )
        await callback.message.answer("Выбери час:", reply_markup=hours_keyboard())
        await callback.answer()


@router.callback_query(F.data.startswith("hour:"))
async def handle_hour(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft: ReminderDraft = data.get("draft")
    if not draft or not draft.target_date:
        await callback.answer("Сначала выбери дату.", show_alert=True)
        return
    hour = int(callback.data.split(":")[1])
    draft.hour = hour
    await state.update_data(draft=draft)
    await state.set_state(ReminderCreation.choosing_minute)
    await callback.message.edit_text(f"Час {hour:02d} сохранён. Теперь минуты:")
    await callback.message.answer("Выбери минуты:", reply_markup=minutes_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("minute:"))
async def handle_minute(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft: ReminderDraft = data.get("draft")
    if not draft or draft.hour is None:
        await callback.answer("Сначала выбери час.", show_alert=True)
        return
    minute = int(callback.data.split(":")[1])
    draft.minute = minute
    await state.update_data(draft=draft)
    await state.set_state(ReminderCreation.choosing_alerts)
    await callback.message.edit_text(
        f"Отлично! Время {draft.hour:02d}:{draft.minute:02d}. Теперь уведомления:",
    )
    await callback.message.answer(
        "Выбери когда напомнить:", reply_markup=alerts_keyboard(draft.alerts)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alert:"))
async def handle_alert_choice(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft: ReminderDraft = data.get("draft")
    if not draft:
        draft = ReminderDraft()
    value = callback.data.split(":")[1]
    if value == "done":
        if not draft.alerts:
            await callback.answer("Выбери хотя бы одно уведомление.", show_alert=True)
            return
        await state.set_state(ReminderCreation.entering_text)
        await callback.message.edit_text("Введи текст напоминания одной строкой.")
        await callback.answer()
        return
    if value in draft.alerts:
        draft.alerts.remove(value)
    else:
        draft.alerts.add(value)
    await state.update_data(draft=draft)
    await callback.message.edit_reply_markup(reply_markup=alerts_keyboard(draft.alerts))
    await callback.answer()


async def finalize_reminder(message: Message, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    draft: ReminderDraft = data.get("draft")
    if not draft or not draft.is_complete:
        await message.answer("Не хватает данных. Давай начнём заново.")
        await state.clear()
        return
    event_dt_utc = draft.build_event_datetime()
    now_local = datetime.now(tz=KYIV_TZ)
    if event_dt_utc <= now_local.astimezone(UTC):
        await message.answer(
            "Это время уже в прошлом. Выбери другое.", reply_markup=reminders_menu_keyboard()
        )
        await state.clear()
        return
    alert_times = compute_alert_datetimes(event_dt_utc, draft.alerts)
    reminder, alerts = await db_manager.create_reminder(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        text=text.strip(),
        event_ts_utc=event_dt_utc,
        created_utc=datetime.now(tz=UTC),
        alert_times_utc=alert_times,
    )
    if scheduler:
        await scheduler.create_jobs_for_alerts(alerts)
    await message.answer("Напоминание сохранено!", reply_markup=reminders_menu_keyboard())
    await message.answer(format_reminder_card(reminder), reply_markup=reminder_actions_keyboard(reminder.id))
    await state.clear()


@router.message(ReminderCreation.entering_text, F.text & ~F.text.startswith("/"))
async def reminder_text_entered(message: Message, state: FSMContext) -> None:
    await finalize_reminder(message, state, message.text)


@router.message(ReminderCreation.entering_text)
async def reminder_text_invalid(message: Message) -> None:
    await message.answer("Напиши текст напоминания одной строкой, пожалуйста.")


async def send_reminder_list(
    message: Message,
    reminders: Sequence[Reminder],
    empty_text: str,
) -> None:
    if not reminders:
        await message.answer(empty_text)
        return
    for reminder in reminders:
        await message.answer(
            format_reminder_card(reminder),
            reply_markup=reminder_actions_keyboard(reminder.id),
        )


@router.message(F.text == "📅 На сегодня")
async def reminders_today(message: Message, state: FSMContext) -> None:
    await state.clear()
    today = datetime.now(tz=KYIV_TZ).date()
    start = datetime.combine(today, time.min, tzinfo=KYIV_TZ).astimezone(UTC)
    end = (datetime.combine(today + timedelta(days=1), time.min, tzinfo=KYIV_TZ).astimezone(UTC))
    reminders = await db_manager.get_reminders_for_range(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        start_utc=start,
        end_utc=end,
        archived=False,
    )
    await send_reminder_list(message, reminders, "На сегодня пока ничего нет.")


@router.message(F.text == "📆 На завтра")
async def reminders_tomorrow(message: Message, state: FSMContext) -> None:
    await state.clear()
    today = datetime.now(tz=KYIV_TZ).date()
    tomorrow = today + timedelta(days=1)
    start = datetime.combine(tomorrow, time.min, tzinfo=KYIV_TZ).astimezone(UTC)
    end = datetime.combine(tomorrow + timedelta(days=1), time.min, tzinfo=KYIV_TZ).astimezone(UTC)
    reminders = await db_manager.get_reminders_for_range(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        start_utc=start,
        end_utc=end,
        archived=False,
    )
    await send_reminder_list(message, reminders, "На завтра планов пока нет.")


@router.message(F.text == "📋 Все")
async def reminders_all(message: Message, state: FSMContext) -> None:
    await state.clear()
    reminders = await db_manager.get_reminders_for_range(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        start_utc=None,
        end_utc=None,
        archived=False,
    )
    await send_reminder_list(message, reminders, "Активных напоминаний нет.")


@router.message(F.text == "📦 Архив")
async def reminders_archive(message: Message, state: FSMContext) -> None:
    await state.clear()
    reminders = await db_manager.get_reminders_for_range(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        start_utc=None,
        end_utc=None,
        archived=True,
    )
    await send_reminder_list(message, reminders, "В архиве пусто.")


@router.callback_query(F.data.startswith("rem:"))
async def reminder_actions(callback: CallbackQuery) -> None:
    if not scheduler:
        await callback.answer("Сервис временно недоступен", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1]
    reminder_id = int(parts[2])
    reminder = await db_manager.get_reminder(reminder_id)
    if not reminder:
        await callback.answer("Напоминание не найдено", show_alert=True)
        return
    if action == "delete":
        await db_manager.archive_reminder(reminder_id)
        await callback.message.edit_text("Напоминание удалено.")
        await callback.answer("Готово")
    else:
        await callback.answer()


@router.message(F.text == "✅ Задачи")
async def tasks_entry(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_tasks_menu(message)


@router.message(F.text == "➕ Создать задачу")
async def task_creation_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(SimpleTextState.awaiting_task_text)
    await message.answer(
        "Напиши задачу одной строкой.", reply_markup=simple_back_keyboard()
    )


@router.message(F.text == "📋 Все задачи")
async def tasks_list(message: Message, state: FSMContext) -> None:
    await state.clear()
    tasks = await db_manager.get_tasks(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
    )
    if not tasks:
        await message.answer("Активных задач пока нет.", reply_markup=tasks_menu_keyboard())
        return
    await message.answer("Активные задачи:", reply_markup=tasks_menu_keyboard())
    for index, task in enumerate(tasks, start=1):
        await message.answer(
            format_task_entry(task, index),
            reply_markup=task_item_keyboard(task.id),
        )


@router.message(SimpleTextState.awaiting_task_text, F.text & ~F.text.startswith("/"))
async def task_received(message: Message, state: FSMContext) -> None:
    await db_manager.create_task(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        text=message.text.strip(),
        created_utc=datetime.now(tz=UTC),
    )
    await state.clear()
    await message.answer("Задача записана!", reply_markup=tasks_menu_keyboard())


@router.message(SimpleTextState.awaiting_task_text)
async def task_invalid(message: Message) -> None:
    await message.answer("Пришли текст задачи без вложений.")


@router.callback_query(F.data.startswith("task:delete:"))
async def task_delete(callback: CallbackQuery) -> None:
    message_obj = callback.message
    if not message_obj:
        await callback.answer("Нет сообщения", show_alert=True)
        return
    try:
        task_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Не удалось определить задачу", show_alert=True)
        return
    task = await db_manager.get_task(task_id)
    if not task or task.chat_id != message_obj.chat.id:
        await callback.answer("Задача не найдена", show_alert=True)
        return
    if task.archived:
        await callback.answer("Уже удалена")
        await message_obj.edit_text(
            f"🗑 {html.escape(task.text)} (id={task.id}) — удалено",
            reply_markup=None,
        )
        return
    removed = await db_manager.archive_task(task_id)
    if removed:
        await message_obj.edit_text(
            f"🗑 {html.escape(task.text)} (id={task.id}) — удалено",
            reply_markup=None,
        )
        await callback.answer("Удалено")
    else:
        await callback.answer("Уже удалена")


@router.message(F.text == "🔁 Ритуалы")
async def rituals_entry(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_rituals_menu(message)


@router.message(F.text == "➕ Добавить ритуал")
async def ritual_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(SimpleTextState.awaiting_ritual_text)
    await message.answer(
        "Запиши ритуал одной строкой, я его сохраню.",
        reply_markup=simple_back_keyboard(),
    )


@router.message(F.text.in_({"🧾 Мои ритуалы", "📜 Мои ритуалы", "📚 Мои ритуалы"}))
async def ritual_list(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Подборка ритуалов:", reply_markup=rituals_menu_keyboard())
    preset_states = await db_manager.get_ritual_preset_states(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
    )
    for preset in RITUAL_PRESETS:
        pinned = preset_states.get(preset.slug, False)
        await message.answer(
            format_ritual_preset_card(preset, pinned),
            reply_markup=ritual_preset_keyboard(preset.slug, pinned),
        )
    rituals = await db_manager.get_rituals(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
    )
    if rituals:
        lines = [f"• {html.escape(ritual.text)} (id={ritual.id})" for ritual in rituals]
        await message.answer("<b>Твои заметки</b>:\n" + "\n".join(lines))
    else:
        await message.answer("Пока своих ритуалов нет. Нажми «➕ Добавить ритуал».")


@router.message(SimpleTextState.awaiting_ritual_text, F.text & ~F.text.startswith("/"))
async def ritual_received(message: Message, state: FSMContext) -> None:
    await db_manager.create_ritual(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        text=message.text.strip(),
        created_utc=datetime.now(tz=UTC),
    )
    await state.clear()
    await message.answer("Сохранил заметку!", reply_markup=rituals_menu_keyboard())


@router.message(SimpleTextState.awaiting_ritual_text)
async def ritual_invalid(message: Message) -> None:
    await message.answer("Жду текстовую заметку.")


@router.callback_query(F.data.startswith("ritual:"))
async def ritual_preset_actions(callback: CallbackQuery) -> None:
    message_obj = callback.message
    if not message_obj:
        await callback.answer("Нет сообщения", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Некорректное действие", show_alert=True)
        return
    action, slug = parts[1], parts[2]
    preset = RITUAL_PRESET_MAP.get(slug)
    if not preset:
        await callback.answer("Пресет не найден", show_alert=True)
        return
    chat_id = message_obj.chat.id
    user_id = callback.from_user.id if callback.from_user else 0
    if action == "add":
        event_dt_utc = get_next_occurrence(preset.recommendation_time)
        reminder_text = f"{preset.title}: {preset.steps}"
        alerts = compute_alert_datetimes(event_dt_utc, ALERT_DEFAULT_SELECTION)
        reminder, new_alerts = await db_manager.create_reminder(
            chat_id=chat_id,
            user_id=user_id,
            text=reminder_text,
            event_ts_utc=event_dt_utc,
            created_utc=datetime.now(tz=UTC),
            alert_times_utc=alerts,
        )
        if scheduler:
            await scheduler.create_jobs_for_alerts(new_alerts)
        await db_manager.set_ritual_preset_state(
            chat_id=chat_id,
            user_id=user_id,
            slug=slug,
            title=preset.title,
            time_hhmm=preset.recommendation_time.strftime("%H:%M"),
            enabled=True,
        )
        await message_obj.edit_text(
            format_ritual_preset_card(preset, True),
            reply_markup=ritual_preset_keyboard(slug, True),
        )
        await message_obj.answer(
            "Напоминание добавлено:\n" + format_reminder_card(reminder),
            reply_markup=reminder_actions_keyboard(reminder.id),
        )
        await callback.answer("Добавлено")
    elif action == "unpin":
        await db_manager.set_ritual_preset_state(
            chat_id=chat_id,
            user_id=user_id,
            slug=slug,
            title=preset.title,
            time_hhmm=preset.recommendation_time.strftime("%H:%M"),
            enabled=False,
        )
        await message_obj.edit_text(
            format_ritual_preset_card(preset, False),
            reply_markup=ritual_preset_keyboard(slug, False),
        )
        await callback.answer("Откреплено")
    else:
        await callback.answer("Неизвестное действие", show_alert=True)


@router.message(F.text == "🛒 Список покупок")
async def shopping_entry(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_shopping_menu(message)


@router.message(F.text == "➕ Добавить позицию")
async def shopping_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(SimpleTextState.awaiting_shopping_text)
    await message.answer(
        "Введи позицию для списка покупок.", reply_markup=simple_back_keyboard()
    )


@router.message(F.text.in_({"📋 Список", "📋 Посмотреть список"}))
async def shopping_list(message: Message, state: FSMContext) -> None:
    await state.clear()
    items = await db_manager.get_shopping_items(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
    )
    if not items:
        await message.answer("Список покупок пуст.", reply_markup=shopping_menu_keyboard())
        return
    await message.answer("Список покупок:", reply_markup=shopping_menu_keyboard())
    for index, item in enumerate(items, start=1):
        await message.answer(
            format_shopping_entry(item, index),
            reply_markup=shopping_item_keyboard(item.id),
        )


@router.message(F.text == "📦 Архив")
async def shopping_archive(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db_manager.prune_shopping_archive()
    items = await db_manager.get_shopping_archive(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
    )
    if not items:
        await message.answer("В архиве пока пусто.", reply_markup=shopping_menu_keyboard())
        return
    await message.answer("Недавние купленные позиции:", reply_markup=shopping_menu_keyboard())
    for index, item in enumerate(items, start=1):
        await message.answer(format_shopping_entry(item, index))


@router.message(F.text.regexp(r"(?i)^купить\s+\d+$"))
async def shopping_mark_purchased(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = message.text.strip()
    try:
        item_id = int(text.split()[1])
    except (IndexError, ValueError):
        await message.answer("Не понял номер позиции.", reply_markup=shopping_menu_keyboard())
        return
    item = await db_manager.get_shopping_item(item_id)
    if not item or item.chat_id != message.chat.id:
        await message.answer("Такой позиции нет.", reply_markup=shopping_menu_keyboard())
        return
    if item.archived:
        await message.answer("Эта позиция уже в архиве.", reply_markup=shopping_menu_keyboard())
        return
    updated = await db_manager.archive_shopping_item(item_id)
    if updated:
        await message.answer("Отмечено как купленное!", reply_markup=shopping_menu_keyboard())
    else:
        await message.answer("Не получилось обновить позицию.", reply_markup=shopping_menu_keyboard())


@router.message(SimpleTextState.awaiting_shopping_text, F.text & ~F.text.startswith("/"))
async def shopping_received(message: Message, state: FSMContext) -> None:
    await db_manager.create_shopping_item(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        text=message.text.strip(),
        created_utc=datetime.now(tz=UTC),
    )
    await state.clear()
    await message.answer("Добавил в список!", reply_markup=shopping_menu_keyboard())


@router.message(SimpleTextState.awaiting_shopping_text)
async def shopping_invalid(message: Message) -> None:
    await message.answer("Пока принимаю только текст.")


@router.callback_query(F.data.startswith("shop:"))
async def shopping_inline_action(callback: CallbackQuery) -> None:
    message_obj = callback.message
    if not message_obj:
        await callback.answer("Нет сообщения", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Некорректное действие", show_alert=True)
        return
    action = parts[1]
    try:
        item_id = int(parts[2])
    except ValueError:
        await callback.answer("Некорректный ID", show_alert=True)
        return
    item = await db_manager.get_shopping_item(item_id)
    if not item or item.chat_id != message_obj.chat.id:
        await callback.answer("Позиция не найдена", show_alert=True)
        return
    if item.archived:
        await callback.answer("Уже обработано")
        await message_obj.edit_text(
            f"{html.escape(item.text)} (id={item.id}) — уже в архиве",
            reply_markup=None,
        )
        return
    updated = await db_manager.archive_shopping_item(item_id)
    if updated:
        status_label = "✅ Куплено" if action == "done" else "❌ Удалено"
        await message_obj.edit_text(
            f"{status_label} • {html.escape(item.text)} (id={item.id})",
            reply_markup=None,
        )
        await callback.answer("Готово")
    else:
        await callback.answer("Уже обработано")


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message) -> None:
    await message.answer("Не понял, выбери пункт меню ниже, пожалуйста.")


async def main() -> None:
    global scheduler
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.delete_webhook(drop_pending_updates=True)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    await db_manager.init()
    scheduler = SchedulerManager(db_manager, bot)
    await scheduler.start()

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
