from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode
from uuid import UUID, uuid4


@dataclass
class User:
    id: UUID
    username: str
    role: str
    password: str
    fullName: str


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
        self.users: dict[str, User] = {}
        self.current_user: User | None = None

    def registerUser(self, username: str, password: str) -> User:
        # Для демонстрации переходов пароль не сохраняется.
        user = User(uuid4(), username, "Участник", "", username)
        self.users[username] = user
        return user

    def login(self, username: str, password: str) -> str:
        self.current_user = self.users.get(username) or self.registerUser(username, password)
        return str(uuid4())


class EventService:
    def __init__(self):
        self.events: dict[UUID, Event] = {}
        self.categories = {
            name: Category(uuid4(), name)
            for name in ("Спорт", "Обучение", "Игры", "Развлечения", "Прогулка")
        }

    def createEvent(self, title: str, desc: str, date: datetime, loc: str) -> Event:
        event = Event(uuid4(), title, desc, date, loc)
        self.events[event.id] = event
        return event

    def updateEventDetails(self, id: UUID, date: datetime, loc: str) -> bool:
        event = self.events.get(id)
        if event is None:
            return False
        event.eventDateTime = date
        event.location = loc
        return True


# Имя сервиса сохранено в написании базовой диаграммы.
class ParticipatnService:
    def __init__(self, authService: AuthService, eventService: EventService):
        self.authService = authService
        self.eventService = eventService
        self.registrations: dict[tuple[UUID, UUID], EventParticipant] = {}

    def registerParticipant(self, userId: UUID, eventId: UUID) -> EventParticipant:
        key = (userId, eventId)
        if key not in self.registrations:
            user = next(user for user in self.authService.users.values() if user.id == userId)
            event = self.eventService.events[eventId]
            self.registrations[key] = EventParticipant(uuid4(), "Иду", user, event)
        return self.registrations[key]

    def cancelRegistration(self, regId: UUID) -> bool:
        key = next((key for key, item in self.registrations.items() if item.id == regId), None)
        if key is None:
            return False
        del self.registrations[key]
        return True


class AnalyticsService:
    def __init__(self, eventService: EventService):
        self.eventService = eventService

    def getEventStats(self, eventId: UUID) -> AnalyticData:
        return self.eventService.events[eventId].analytics


@dataclass
class Notification:
    id: UUID
    description: str
    event: Event
    user: User


class NotificationService:
    def __init__(self, authService: AuthService, eventService: EventService):
        self.authService = authService
        self.eventService = eventService
        self.notifications: list[Notification] = []

    def sendMsg(self, eventId: UUID) -> Notification:
        user = self.authService.current_user
        if user is None:
            raise ValueError("Войдите в систему")
        event = self.eventService.events[eventId]
        notification = Notification(uuid4(), f"Напоминание о мероприятии «{event.title}»", event, user)
        self.notifications.append(notification)
        return notification


class IMapProvider(ABC):
    @abstractmethod
    def getMapUrl(self, location: str) -> str:
        ...


class YandexMapsAdapter(IMapProvider):
    def __init__(self, apiKey: str = ""):
        self.apiKey = apiKey

    def getMapUrl(self, location: str) -> str:
        return "https://yandex.ru/maps/?" + urlencode({"text": location})


class IntegrationService:
    def __init__(self, mapProvider: IMapProvider):
        self.mapProvider = mapProvider

    def showMapWidget(self, location: str) -> str:
        return self.mapProvider.getMapUrl(location)

    def exportEventToCalendar(self, title: str, date: datetime) -> str:
        return ""


@dataclass
class Comment:
    id: UUID
    text: str
    author: User


class CommentValidator:
    def __init__(self, stopWords: list[str]):
        self.stopWords = stopWords

    def validateText(self, text: str) -> None:
        if not text.strip() or any(word.casefold() in text.casefold() for word in self.stopWords):
            raise ValueError("Недопустимый текст комментария")


class CommentService:
    def __init__(self, validator: CommentValidator):
        self.validator = validator
        self.event: Event | None = None
        self.author: User | None = None
        self.comments: dict[UUID, list[Comment]] = {}

    def postComment(self, text: str) -> Comment:
        self.validator.validateText(text)
        if self.event is None or self.author is None:
            raise ValueError("Выберите мероприятие и войдите в систему")
        comment = Comment(uuid4(), text, self.author)
        self.comments.setdefault(self.event.id, []).append(comment)
        return comment

    def getCommentsForEv1nt(self) -> list[Comment]:
        return list(self.comments.get(self.event.id, [])) if self.event else []
