from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac
from hmac import compare_digest
import secrets
from urllib.parse import urlencode
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

ROLES = ("Участник", "Организатор", "Администратор")
CATEGORIES = ("Спорт", "Обучение", "Игры", "Развлечения", "Прогулка")


def minsk_now() -> datetime:
    """Возвращает местное время Минска без смещения для сравнения с датой мероприятия."""
    return datetime.now(ZoneInfo("Europe/Minsk")).replace(tzinfo=None)


@dataclass
class User:
    id: UUID
    username: str
    role: str
    password: str
    fullName: str
    active: bool = True


@dataclass
class AnalyticData:
    id: UUID = field(default_factory=uuid4)
    viewsCount: int = 0


@dataclass
class Event:
    id: UUID
    title: str
    description: str
    eventDateTime: datetime
    location: str
    analytics: AnalyticData = field(default_factory=AnalyticData)
    organizer: User | None = None


@dataclass
class Category:
    id: UUID
    name: str
    events: list[Event] = field(default_factory=list)


@dataclass
class EventParticipant:
    id: UUID
    status: str
    user: User
    event: Event


class AuthService:
    def __init__(self):
        """Создаёт хранилище пользователей, сессий и контекст текущего запроса."""
        self.users: dict[str, User] = {}
        self.current_user: User | None = None
        self.sessions: dict[str, dict] = {}

    def registerUser(self, username: str, password: str, role="Участник", fullName="", *, bootstrap=False) -> User:
        """Регистрирует уникальный логин и хранит солёный хеш пароля; первого администратора создаёт настройка."""
        username = username.strip()
        if not username or len(username) > 100:
            raise ValueError("Введите логин длиной от 1 до 100 символов.")
        if any(name.casefold() == username.casefold() for name in self.users):
            raise ValueError("Этот логин уже зарегистрирован.")
        if not password or len(password) > 1024:
            raise ValueError("Введите непустой пароль.")
        if role not in ROLES or (role == "Администратор" and not (bootstrap and not self.users)):
            raise PermissionError("Администратора назначает действующий администратор.")
        salt = secrets.token_hex(16)
        digest = pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
        user = User(uuid4(), username, role, f"{salt}${digest}", fullName.strip()[:150] or username)
        self.users[username] = user
        return user

    def login(self, username: str, password: str) -> str:
        """Проверяет пароль и состояние пользователя, создаёт отдельную сессию на 12 часов."""
        user = next((u for u in self.users.values() if u.username.casefold() == username.strip().casefold()), None)
        if user is None or not user.active:
            raise ValueError("Неверный логин или пароль.")
        salt, expected = user.password.split("$")
        actual = pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
        if not compare_digest(actual, expected):
            raise ValueError("Неверный логин или пароль.")
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"user": str(user.id), "expires": (datetime.now(timezone.utc) + timedelta(hours=12)).timestamp(),
                                "csrf": secrets.token_urlsafe(32)}
        self.current_user = user
        return token

    def restoreSession(self, token: str) -> User | None:
        """Восстанавливает пользователя только по действующей сессии текущего браузера."""
        self.current_user = None
        session = self.sessions.get(token)
        if session and session["expires"] > datetime.now(timezone.utc).timestamp():
            self.current_user = next((u for u in self.users.values() if str(u.id) == session["user"] and u.active), None)
        return self.current_user

    def requireRole(self, *roles) -> User:
        """Возвращает текущего пользователя либо запрещает действие для его роли."""
        if not self.current_user or not self.current_user.active or self.current_user.role not in roles:
            raise PermissionError("Недостаточно прав для этого действия.")
        return self.current_user



class EventService:
    def __init__(self, authService: AuthService | None = None):
        """Создаёт каталог и подключает проверку прав пользователей."""
        self.authService = authService
        self.events: dict[UUID, Event] = {}
        self.categories = {name: Category(uuid4(), name) for name in CATEGORIES}

    def requireOwner(self, event: Event | None = None) -> User:
        """Разрешает создание организатору и администратору, изменение — владельцу или администратору."""
        if self.authService is None:
            raise PermissionError("Сервис авторизации не подключён.")
        user = self.authService.requireRole("Организатор", "Администратор")
        if event and user.role != "Администратор" and (not event.organizer or event.organizer.id != user.id):
            raise PermissionError("Изменять мероприятие может только его организатор или администратор.")
        return user

    @staticmethod
    def validateDetails(title: str, date: datetime, loc: str) -> None:
        """Проверяет название, место и дату на сервере до изменения мероприятия."""
        if not title.strip() or len(title) > 200 or not loc.strip() or len(loc) > 300:
            raise ValueError("Введите название (до 200 символов) и место (до 300 символов).")
        if not isinstance(date, datetime) or date.tzinfo is not None:
            raise ValueError("Укажите местные дату и время проведения (Минск).")

    def createEvent(self, title: str, desc: str, date: datetime, loc: str, category="Спорт") -> Event:
        """Проверяет права и поля, создаёт событие и сразу связывает с организатором и категорией."""
        owner = self.requireOwner()
        self.validateDetails(title, date, loc)
        if category not in self.categories:
            raise ValueError("Выберите существующую категорию.")
        event = Event(uuid4(), title.strip(), desc.strip(), date, loc.strip(), organizer=owner)
        self.events[event.id] = event
        self.categories[category].events.append(event)
        return event

    def updateEventDetails(self, id: UUID, date: datetime, loc: str) -> bool:
        """Обновляет время и место события после проверки владельца; сообщает об отсутствии события."""
        event = self.events.get(id)
        if event is None:
            return False
        self.requireOwner(event)
        self.validateDetails(event.title, date, loc)
        event.eventDateTime, event.location = date, loc.strip()
        return True


    def deleteEvent(self, id: UUID) -> None:
        """Удаляет событие и его категорийную связь; разрешено только администратору."""
        self.authService.requireRole("Администратор")
        del self.events[id]
        for group in self.categories.values():
            group.events[:] = [event for event in group.events if event.id != id]

    def categoryFor(self, event: Event) -> str:
        """Возвращает категорию события без ошибки при отсутствии связи."""
        return next((name for name, group in self.categories.items() if event in group.events), "Спорт")


# Написание имени сохранено из диаграммы Башуры.
class ParticipatnService:
    def __init__(self, authService: AuthService, eventService: EventService):
        """Подключает пользователей и события, создаёт хранилище регистраций."""
        self.authService, self.eventService = authService, eventService
        self.registrations: dict[tuple[UUID, UUID], EventParticipant] = {}

    def registerParticipant(self, userId: UUID, eventId: UUID) -> EventParticipant:
        """Записывает текущего пользователя на событие без повторных регистраций."""
        user = self.authService.requireRole(*ROLES)
        if user.id != userId:
            raise PermissionError("Можно записать только себя.")
        event = self.eventService.events[eventId]
        key = (userId, eventId)
        if key not in self.registrations:
            self.registrations[key] = EventParticipant(uuid4(), "Иду", user, event)
        return self.registrations[key]

    def cancelRegistration(self, regId: UUID) -> bool:
        """Отменяет собственную регистрацию; администратор может отменить любую."""
        user = self.authService.requireRole(*ROLES)
        key = next((key for key, item in self.registrations.items() if item.id == regId), None)
        if key is None:
            return False
        if key[0] != user.id and user.role != "Администратор":
            raise PermissionError("Нельзя отменить чужую регистрацию.")
        del self.registrations[key]
        return True


class AnalyticsService:
    def __init__(self, eventService: EventService):
        """Подключает каталог для подсчёта просмотров и получения статистики."""
        self.eventService = eventService

    def recordView(self, eventId: UUID) -> None:
        """Увеличивает число открытий страницы деталей мероприятия."""
        self.eventService.events[eventId].analytics.viewsCount += 1

    def getEventStats(self, eventId: UUID) -> AnalyticData:
        """Возвращает статистику только организатору данного события или администратору."""
        event = self.eventService.events[eventId]
        self.eventService.requireOwner(event)
        return event.analytics


@dataclass
class Notification:
    id: UUID
    description: str
    event: Event
    user: User
    kind: str = "message"
    read: bool = False


class NotificationService:
    def __init__(self, authService: AuthService, eventService: EventService):
        """Создаёт хранилище уведомлений расширения; расписание в отчёте не задано."""
        self.authService, self.eventService = authService, eventService
        self.notifications: list[Notification] = []
        self.subscriptions: set[tuple[UUID, UUID]] = set()
        self.delivered: set[tuple[UUID, UUID, str]] = set()

    def sendMsg(self, eventId: UUID) -> Notification:
        """Создаёт внутреннее напоминание для вошедшего пользователя."""
        user = self.authService.requireRole(*ROLES)
        event = self.eventService.events[eventId]
        notification = Notification(uuid4(), f"Напоминание о мероприятии «{event.title}»", event, user, "reminder")
        self.notifications.append(notification)
        return notification





class IMapProvider(ABC):
    @abstractmethod
    def getMapUrl(self, location: str) -> str:
        """Задаёт контракт получения ссылки на карту по адресу."""
        ...


class YandexMapsAdapter(IMapProvider):
    def __init__(self, apiKey: str = ""):
        """Сохраняет необязательный ключ для будущего расширения картографического адаптера."""
        self.apiKey = apiKey

    def getMapUrl(self, location: str) -> str:
        """Возвращает ссылку на поиск места в Яндекс Картах без необходимости API-ключа."""
        return "https://yandex.ru/maps/?" + urlencode({"text": location})


class IntegrationService:
    def __init__(self, mapProvider: IMapProvider):
        """Подключает провайдер карт."""
        self.mapProvider = mapProvider

    def showMapWidget(self, location: str) -> str:
        """Возвращает адрес карты для открытия в браузере."""
        return self.mapProvider.getMapUrl(location)

    @staticmethod
    def calendarText(value: str) -> str:
        """Экранирует текстовые поля файла iCalendar и исключает внедрение новых свойств."""
        return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")

    def exportEventToCalendar(self, title: str, date: datetime, event: Event | None = None) -> str:
        """Создаёт файл iCalendar с датой события в часовом поясе Минска (UTC+3)."""
        start = date.replace(tzinfo=ZoneInfo("Europe/Minsk")).astimezone(timezone.utc)
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Event Platform//RU", "BEGIN:VEVENT",
                 f"UID:{event.id if event else uuid4()}@event-platform.local",
                 f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}", f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
                 f"SUMMARY:{self.calendarText(title)}"]
        if event:
            lines += [f"LOCATION:{self.calendarText(event.location)}", f"DESCRIPTION:{self.calendarText(event.description)}"]
        lines += ["END:VEVENT", "END:VCALENDAR"]
        folded = []
        for line in lines:
            chunk = ""
            for char in line:
                if len((chunk + char).encode("utf-8")) > 73:
                    folded.append(chunk)
                    chunk = " "
                chunk += char
            folded.append(chunk)
        return "\r\n".join(folded) + "\r\n"


@dataclass
class Comment:
    id: UUID
    text: str
    author: User


class CommentValidator:
    def __init__(self, stopWords: list[str]):
        """Сохраняет запрещённые слова для проверки комментариев."""
        self.stopWords = stopWords

    def validateText(self, text: str) -> None:
        """Отклоняет пустые, слишком длинные и содержащие запрещённые подстроки комментарии."""
        if not text.strip() or len(text) > 3000 or any(word.casefold() in text.casefold() for word in self.stopWords):
            raise ValueError("Введите допустимый комментарий длиной до 3000 символов.")


class CommentService:
    def __init__(self, validator: CommentValidator):
        """Создаёт контекст публикации и хранилище комментариев по событиям."""
        self.validator = validator
        self.event: Event | None = None
        self.author: User | None = None
        self.comments: dict[UUID, list[Comment]] = {}

    def postComment(self, text: str) -> Comment:
        """Проверяет и сохраняет комментарий выбранного автора к текущему событию."""
        self.validator.validateText(text)
        if self.event is None or self.author is None or not self.author.active:
            raise ValueError("Выберите мероприятие и войдите в систему.")
        comment = Comment(uuid4(), text.strip(), self.author)
        self.comments.setdefault(self.event.id, []).append(comment)
        return comment

    def getCommentsForEv1nt(self) -> list[Comment]:
        """Возвращает копию списка комментариев выбранного события."""
        return list(self.comments.get(self.event.id, [])) if self.event else []
